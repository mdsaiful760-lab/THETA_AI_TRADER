"""In-process publish/subscribe event bus for THETA AI TRADER.

The event bus is the communication backbone of the platform. Producers publish
immutable :class:`EventEnvelope` instances on hierarchical topics; consumers
register handlers without coupling to publishers. Dispatch is synchronous by
default, thread-safe, and isolates subscriber failures.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

from core.event_topics import EventTopics

EVENT_BUS_VERSION: str = "1.0.0"
EVENT_ENVELOPE_SCHEMA_VERSION: str = "1.0.0"

ERROR_PUBLISH_INVALID_TOPIC: str = "EVENT_BUS.PUBLISH.INVALID_TOPIC"
ERROR_PUBLISH_INVALID_CORRELATION_ID: str = "EVENT_BUS.PUBLISH.INVALID_CORRELATION_ID"
ERROR_PUBLISH_NAIVE_TIMESTAMP: str = "EVENT_BUS.PUBLISH.NAIVE_TIMESTAMP"
ERROR_PUBLISH_MISSING_PAYLOAD: str = "EVENT_BUS.PUBLISH.MISSING_PAYLOAD"
ERROR_PUBLISH_BUS_SHUTDOWN: str = "EVENT_BUS.PUBLISH.BUS_SHUTDOWN"
ERROR_SUBSCRIBE_INVALID_PATTERN: str = "EVENT_BUS.SUBSCRIBE.INVALID_PATTERN"
ERROR_DISPATCH_HANDLER_FAILED: str = "EVENT_BUS.DISPATCH.HANDLER_FAILED"
ERROR_DISPATCH_RECURSION_LIMIT: str = "EVENT_BUS.DISPATCH.RECURSION_LIMIT"

_TOPIC_SEGMENT_RE = re.compile(r"^[a-z0-9_]+$")
_PUBLISH_TOPIC_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
_PATTERN_RE = re.compile(r"^(\*|[a-z0-9_]+(\.[a-z0-9_]+)*(\.\*)?)$")

_logger = logging.getLogger("core.event_bus")

_MISSING = object()


class DispatchMode(Enum):
    """Event dispatch strategy."""

    SYNC = "sync"
    ASYNC_BOUNDED = "async_bounded"


class QueueFullPolicy(Enum):
    """Behavior when an async dispatch queue is full."""

    BLOCK = "block"
    DROP_OLDEST = "drop_oldest"
    RAISE = "raise"


class SubscriptionState(Enum):
    """Lifecycle state of an event subscription."""

    ACTIVE = "active"
    PAUSED = "paused"
    UNSUBSCRIBED = "unsubscribed"


@dataclass(frozen=True)
class EventEnvelope:
    """Immutable wrapper for a published platform event.

    Attributes:
        event_id: Unique event identifier (UUID v4).
        topic: Hierarchical topic string.
        payload: Immutable domain object reference.
        correlation_id: Pipeline- or request-level correlation identifier.
        causation_id: Optional prior event identifier that caused this event.
        producer: Publishing component name.
        producer_version: Optional semantic version of the producer.
        occurred_at: Timezone-aware timestamp when the domain fact occurred.
        published_at: Timezone-aware timestamp when the bus accepted the event.
        schema_version: Envelope schema semver.
        payload_type: Optional fully-qualified payload type name.
        tags: Optional immutable key-value labels.
    """

    event_id: str
    topic: str
    payload: object
    correlation_id: str
    producer: str
    occurred_at: datetime
    published_at: datetime
    schema_version: str = EVENT_ENVELOPE_SCHEMA_VERSION
    causation_id: str | None = None
    producer_version: str | None = None
    payload_type: str | None = None
    tags: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class SubscriptionHandle:
    """Opaque handle returned when subscribing to a topic pattern.

    Attributes:
        subscription_id: Unique subscription identifier.
        topic: Registered topic pattern.
        state: Current subscription lifecycle state.
        priority: Dispatch priority (higher runs first).
        registered_at: Timezone-aware registration timestamp.
        handler_id: Debug identifier for the registered handler.
    """

    subscription_id: str
    topic: str
    state: SubscriptionState
    priority: int
    registered_at: datetime
    handler_id: str | None = None


@dataclass(frozen=True)
class SubscriberFailure:
    """Diagnostic payload for ``event_bus.subscriber.failed`` events.

    Attributes:
        subscription_id: Subscription that raised the failure.
        topic: Topic of the event being handled.
        event_id: Identifier of the event that triggered the handler.
        error_type: Exception class name.
        message: Human-readable failure message.
    """

    subscription_id: str
    topic: str
    event_id: str
    error_type: str
    message: str


@runtime_checkable
class EventHandler(Protocol):
    """Callable invoked when a matching event is published."""

    def __call__(self, event: EventEnvelope) -> None:
        """Handle a published event."""
        ...


@runtime_checkable
class MetricsRecorder(Protocol):
    """Optional metrics sink for event bus instrumentation."""

    def increment_counter(
        self,
        name: str,
        *,
        labels: Mapping[str, str] | None = None,
        value: float = 1.0,
    ) -> None:
        """Increment a counter metric."""

    def observe_histogram(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Record a histogram observation."""

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Set a gauge metric."""


class _NoOpMetricsRecorder:
    """Default metrics recorder that discards all recordings."""

    def increment_counter(
        self,
        name: str,
        *,
        labels: Mapping[str, str] | None = None,
        value: float = 1.0,
    ) -> None:
        return None

    def observe_histogram(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        return None

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        return None


class EventBusError(Exception):
    """Root of all event bus exceptions.

    Attributes:
        code: Stable machine-readable error code.
    """

    def __init__(self, message: str, *, code: str) -> None:
        """Initialize an event bus error.

        Args:
            message: Human-readable error description.
            code: Stable machine-readable error code.
        """
        super().__init__(message)
        self.code = code


class EventBusConfigurationError(EventBusError):
    """Raised when static event bus configuration is invalid."""


class EventBusPublishError(EventBusError):
    """Raised when an event cannot be published."""


class EventBusSubscribeError(EventBusError):
    """Raised when a subscription request is invalid."""


@dataclass(frozen=True)
class EventBusPolicy:
    """Configuration for :class:`EventBus` behavior.

    Attributes:
        dispatch_mode: Dispatch strategy; v1 implements ``SYNC`` only.
        queue_max_size: Capacity for optional async dispatch queue.
        queue_full_policy: Queue overflow behavior for async mode.
        max_recursion_depth: Maximum nested publish depth from handlers.
        allow_global_wildcard: Whether ``*`` subscription patterns are allowed.
        allow_clear: Enable test-only :meth:`EventBus.clear`.
        emit_subscriber_failure_events: Emit diagnostic events on handler errors.
        handler_timeout_ms: Optional per-handler timeout (not implemented in v1).
    """

    dispatch_mode: DispatchMode = DispatchMode.SYNC
    queue_max_size: int = 10_000
    queue_full_policy: QueueFullPolicy = QueueFullPolicy.RAISE
    max_recursion_depth: int = 3
    allow_global_wildcard: bool = False
    allow_clear: bool = False
    emit_subscriber_failure_events: bool = True
    handler_timeout_ms: int | None = None

    def __post_init__(self) -> None:
        """Validate policy invariants."""
        if self.queue_max_size <= 0:
            raise EventBusConfigurationError(
                "queue_max_size must be positive",
                code="EVENT_BUS.CONFIG.INVALID",
            )
        if self.max_recursion_depth < 0:
            raise EventBusConfigurationError(
                "max_recursion_depth must be non-negative",
                code="EVENT_BUS.CONFIG.INVALID",
            )
        if self.dispatch_mode is not DispatchMode.SYNC:
            raise EventBusConfigurationError(
                "Only SYNC dispatch mode is implemented in v1",
                code="EVENT_BUS.CONFIG.UNSUPPORTED_DISPATCH_MODE",
            )


@dataclass
class _Subscription:
    """Internal mutable subscription record."""

    subscription_id: str
    pattern: str
    handler: EventHandler
    priority: int
    registration_order: int
    state: SubscriptionState
    registered_at: datetime
    handler_id: str | None


class EventBus:
    """Thread-safe in-process publish/subscribe event bus."""

    def __init__(
        self,
        policy: EventBusPolicy | None = None,
        *,
        metrics_recorder: MetricsRecorder | None = None,
    ) -> None:
        """Construct an event bus.

        Args:
            policy: Optional dispatch and safety policy. Defaults apply when
                omitted.
            metrics_recorder: Optional metrics sink. A no-op recorder is used
                when omitted.
        """
        self._policy = policy or EventBusPolicy()
        self._metrics = metrics_recorder or _NoOpMetricsRecorder()
        self._lock = threading.RLock()
        self._subscriptions: list[_Subscription] = []
        self._registration_counter = 0
        self._shutdown = False
        self._local = threading.local()
        self._emitting_failure_event = False

    @property
    def policy(self) -> EventBusPolicy:
        """Return the bus configuration policy."""
        return self._policy

    def publish(
        self,
        envelope_or_topic: EventEnvelope | str,
        payload: object = _MISSING,
        *,
        correlation_id: str = "",
        producer: str = "",
        occurred_at: datetime | None = None,
        causation_id: str | None = None,
        producer_version: str | None = None,
        payload_type: str | None = None,
        tags: Mapping[str, str] | None = None,
        event_id: str | None = None,
    ) -> str:
        """Publish an event to matching subscribers.

        Args:
            envelope_or_topic: Either a fully built :class:`EventEnvelope` or
                a topic string for the convenience builder form.
            payload: Event payload when using the convenience builder form.
            correlation_id: Correlation identifier for the convenience form.
            producer: Producer name for the convenience form.
            occurred_at: Domain timestamp for the convenience form.
            causation_id: Optional prior event identifier.
            producer_version: Optional producer semantic version.
            payload_type: Optional payload type name for diagnostics.
            tags: Optional immutable labels for the convenience form.
            event_id: Optional explicit event identifier.

        Returns:
            The published event identifier.

        Raises:
            EventBusPublishError: When validation fails or the bus is shut down.
        """
        if isinstance(envelope_or_topic, EventEnvelope):
            envelope = envelope_or_topic
        else:
            if payload is _MISSING:
                raise EventBusPublishError(
                    "payload is required when publishing by topic",
                    code=ERROR_PUBLISH_MISSING_PAYLOAD,
                )
            if occurred_at is None:
                occurred_at = _utc_now()
            resolved_payload_type = payload_type
            if resolved_payload_type is None and payload is not None:
                resolved_payload_type = type(payload).__module__ + "." + type(
                    payload
                ).__qualname__
            envelope = EventEnvelope(
                event_id=event_id or str(uuid.uuid4()),
                topic=envelope_or_topic,
                payload=payload,
                correlation_id=correlation_id,
                producer=producer,
                occurred_at=occurred_at,
                published_at=_utc_now(),
                causation_id=causation_id,
                producer_version=producer_version,
                payload_type=resolved_payload_type,
                tags=_freeze_tags(tags),
            )

        self._validate_envelope(envelope)

        depth = getattr(self._local, "publish_depth", 0)
        if depth >= self._policy.max_recursion_depth:
            self._metrics.increment_counter("event_bus_recursion_limit_total")
            _logger.warning(
                "event_bus.publish.rejected",
                extra={
                    "code": ERROR_DISPATCH_RECURSION_LIMIT,
                    "topic": envelope.topic,
                    "event_id": envelope.event_id,
                    "correlation_id": envelope.correlation_id,
                },
            )
            raise EventBusPublishError(
                "maximum publish recursion depth exceeded",
                code=ERROR_DISPATCH_RECURSION_LIMIT,
            )

        self._local.publish_depth = depth + 1
        try:
            _logger.debug(
                "event_bus.publish",
                extra={
                    "topic": envelope.topic,
                    "event_id": envelope.event_id,
                    "correlation_id": envelope.correlation_id,
                    "producer": envelope.producer,
                },
            )
            self._metrics.increment_counter(
                "event_bus_publish_total",
                labels={"topic": envelope.topic, "producer": envelope.producer},
            )
            self._dispatch(envelope)
            return envelope.event_id
        finally:
            self._local.publish_depth = depth

    def subscribe(
        self,
        topic: str,
        handler: EventHandler,
        *,
        priority: int = 0,
    ) -> SubscriptionHandle:
        """Register a handler for a topic pattern.

        Args:
            topic: Exact topic or pattern ending in ``.*`` or global ``*``.
            handler: Callable invoked for matching events.
            priority: Dispatch priority; higher values run first.

        Returns:
            A :class:`SubscriptionHandle` for lifecycle management.

        Raises:
            EventBusSubscribeError: When the topic pattern is invalid.
        """
        normalized = _validate_subscription_pattern(
            topic,
            allow_global_wildcard=self._policy.allow_global_wildcard,
        )

        with self._lock:
            self._registration_counter += 1
            registration_order = self._registration_counter
            subscription_id = str(uuid.uuid4())
            handler_id = getattr(handler, "__qualname__", repr(handler))
            registered_at = _utc_now()
            record = _Subscription(
                subscription_id=subscription_id,
                pattern=normalized,
                handler=handler,
                priority=priority,
                registration_order=registration_order,
                state=SubscriptionState.ACTIVE,
                registered_at=registered_at,
                handler_id=handler_id,
            )
            self._subscriptions.append(record)
            self._update_subscription_gauge()

        _logger.info(
            "event_bus.subscribe",
            extra={"topic": normalized, "handler_id": handler_id},
        )
        return SubscriptionHandle(
            subscription_id=subscription_id,
            topic=normalized,
            state=SubscriptionState.ACTIVE,
            priority=priority,
            registered_at=registered_at,
            handler_id=handler_id,
        )

    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        """Remove a subscription.

        Idempotent: unknown or already-unsubscribed handles are ignored.

        Args:
            handle: Handle returned from :meth:`subscribe`.
        """
        with self._lock:
            record = self._find_subscription(handle.subscription_id)
            if record is None or record.state is SubscriptionState.UNSUBSCRIBED:
                return
            record.state = SubscriptionState.UNSUBSCRIBED
            self._subscriptions = [
                sub
                for sub in self._subscriptions
                if sub.state is not SubscriptionState.UNSUBSCRIBED
            ]
            self._update_subscription_gauge()

        _logger.info(
            "event_bus.unsubscribe",
            extra={
                "topic": handle.topic,
                "subscription_id": handle.subscription_id,
            },
        )

    def pause(self, handle: SubscriptionHandle) -> None:
        """Pause delivery to a subscription.

        Args:
            handle: Handle returned from :meth:`subscribe`.
        """
        with self._lock:
            record = self._find_subscription(handle.subscription_id)
            if record is None or record.state is SubscriptionState.UNSUBSCRIBED:
                return
            record.state = SubscriptionState.PAUSED

    def resume(self, handle: SubscriptionHandle) -> None:
        """Resume a paused subscription.

        Args:
            handle: Handle returned from :meth:`subscribe`.
        """
        with self._lock:
            record = self._find_subscription(handle.subscription_id)
            if record is None or record.state is SubscriptionState.UNSUBSCRIBED:
                return
            record.state = SubscriptionState.ACTIVE

    def subscriber_count(self, topic: str | None = None) -> int:
        """Return the number of active subscriptions.

        Args:
            topic: When provided, count subscriptions matching this topic.
                When omitted, count all non-unsubscribed subscriptions.

        Returns:
            Number of matching subscriptions.
        """
        with self._lock:
            active = [
                sub
                for sub in self._subscriptions
                if sub.state is not SubscriptionState.UNSUBSCRIBED
            ]
            if topic is None:
                return len(active)
            return sum(
                1 for sub in active if _topic_matches(sub.pattern, topic)
            )

    def clear(self) -> None:
        """Remove all subscriptions.

        Raises:
            EventBusConfigurationError: When ``allow_clear`` is disabled.
        """
        if not self._policy.allow_clear:
            raise EventBusConfigurationError(
                "clear() is disabled; enable allow_clear in EventBusPolicy",
                code="EVENT_BUS.CONFIG.CLEAR_DISABLED",
            )
        with self._lock:
            self._subscriptions.clear()
            self._update_subscription_gauge()

    def shutdown(self) -> None:
        """Reject new publishes and mark the bus as shut down."""
        self._shutdown = True
        _logger.info("event_bus.shutdown")

    def _validate_envelope(self, envelope: EventEnvelope) -> None:
        """Validate envelope invariants before dispatch."""
        if self._shutdown:
            raise EventBusPublishError(
                "event bus is shut down",
                code=ERROR_PUBLISH_BUS_SHUTDOWN,
            )
        if not _is_valid_publish_topic(envelope.topic):
            raise EventBusPublishError(
                f"invalid topic: {envelope.topic!r}",
                code=ERROR_PUBLISH_INVALID_TOPIC,
            )
        if not envelope.correlation_id or not envelope.correlation_id.strip():
            raise EventBusPublishError(
                "correlation_id must be non-empty",
                code=ERROR_PUBLISH_INVALID_CORRELATION_ID,
            )
        if envelope.payload is None:
            raise EventBusPublishError(
                "payload must not be None",
                code=ERROR_PUBLISH_MISSING_PAYLOAD,
            )
        if not _is_timezone_aware(envelope.occurred_at):
            raise EventBusPublishError(
                "occurred_at must be timezone-aware",
                code=ERROR_PUBLISH_NAIVE_TIMESTAMP,
            )
        if not _is_timezone_aware(envelope.published_at):
            raise EventBusPublishError(
                "published_at must be timezone-aware",
                code=ERROR_PUBLISH_NAIVE_TIMESTAMP,
            )

    def _dispatch(self, envelope: EventEnvelope) -> None:
        """Deliver an event to matching subscribers."""
        start = time.perf_counter()
        with self._lock:
            matching = [
                sub
                for sub in self._subscriptions
                if sub.state is not SubscriptionState.UNSUBSCRIBED
                and _topic_matches(sub.pattern, envelope.topic)
            ]
            snapshot = sorted(
                matching,
                key=lambda sub: (-sub.priority, sub.registration_order),
            )

        _logger.debug(
            "event_bus.dispatch.start",
            extra={
                "topic": envelope.topic,
                "event_id": envelope.event_id,
                "count": len(snapshot),
            },
        )

        delivered = 0
        for sub in snapshot:
            if sub.state is SubscriptionState.PAUSED:
                continue
            delivered += 1
            self._invoke_handler(sub, envelope)

        duration_ms = (time.perf_counter() - start) * 1000.0
        self._metrics.observe_histogram(
            "event_bus_dispatch_duration_seconds",
            duration_ms / 1000.0,
            labels={"topic": envelope.topic},
        )
        _logger.debug(
            "event_bus.dispatch.complete",
            extra={
                "topic": envelope.topic,
                "event_id": envelope.event_id,
                "duration_ms": duration_ms,
                "count": delivered,
            },
        )

    def _invoke_handler(
        self,
        subscription: _Subscription,
        envelope: EventEnvelope,
    ) -> None:
        """Invoke one subscriber with error isolation."""
        handler_start = time.perf_counter()
        try:
            subscription.handler(envelope)
        except Exception as exc:  # noqa: BLE001 - subscriber isolation boundary
            self._handle_subscriber_failure(subscription, envelope, exc)
        else:
            self._metrics.observe_histogram(
                "event_bus_handler_duration_seconds",
                time.perf_counter() - handler_start,
                labels={
                    "topic": envelope.topic,
                    "handler": subscription.handler_id or "unknown",
                },
            )
        self._metrics.increment_counter(
            "event_bus_deliveries_total",
            labels={
                "topic": envelope.topic,
                "subscription_id": subscription.subscription_id,
            },
        )

    def _handle_subscriber_failure(
        self,
        subscription: _Subscription,
        envelope: EventEnvelope,
        exc: Exception,
    ) -> None:
        """Record and optionally emit a diagnostic event for handler failure."""
        handler_id = subscription.handler_id or "unknown"
        _logger.error(
            "event_bus.handler.failed",
            extra={
                "code": ERROR_DISPATCH_HANDLER_FAILED,
                "topic": envelope.topic,
                "event_id": envelope.event_id,
                "subscription_id": subscription.subscription_id,
                "handler_id": handler_id,
                "error_type": type(exc).__name__,
            },
            exc_info=exc,
        )
        self._metrics.increment_counter(
            "event_bus_subscriber_failures_total",
            labels={"topic": envelope.topic, "handler": handler_id},
        )

        if (
            not self._policy.emit_subscriber_failure_events
            or self._emitting_failure_event
        ):
            return

        depth = getattr(self._local, "publish_depth", 0)
        if depth >= self._policy.max_recursion_depth:
            return

        failure = SubscriberFailure(
            subscription_id=subscription.subscription_id,
            topic=envelope.topic,
            event_id=envelope.event_id,
            error_type=type(exc).__name__,
            message=str(exc),
        )
        diagnostic = EventEnvelope(
            event_id=str(uuid.uuid4()),
            topic=EventTopics.SUBSCRIBER_FAILED,
            payload=failure,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.event_id,
            producer="event_bus",
            occurred_at=_utc_now(),
            published_at=_utc_now(),
            payload_type=SubscriberFailure.__qualname__,
        )

        self._emitting_failure_event = True
        try:
            self._dispatch(diagnostic)
        finally:
            self._emitting_failure_event = False

    def _find_subscription(
        self,
        subscription_id: str,
    ) -> _Subscription | None:
        """Find a subscription record by identifier."""
        for sub in self._subscriptions:
            if sub.subscription_id == subscription_id:
                return sub
        return None

    def _update_subscription_gauge(self) -> None:
        """Refresh active subscription gauge metric."""
        active_count = sum(
            1
            for sub in self._subscriptions
            if sub.state is not SubscriptionState.UNSUBSCRIBED
        )
        self._metrics.set_gauge(
            "event_bus_active_subscriptions",
            float(active_count),
        )


def _utc_now() -> datetime:
    """Return the current UTC timestamp with timezone information."""
    return datetime.now(timezone.utc)


def _freeze_tags(tags: Mapping[str, str] | None) -> Mapping[str, str]:
    """Return an immutable mapping of event tags."""
    if tags is None:
        return MappingProxyType({})
    return MappingProxyType(dict(tags))


def _is_timezone_aware(value: datetime) -> bool:
    """Return whether a datetime carries timezone information."""
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _is_valid_publish_topic(topic: str) -> bool:
    """Return whether a topic string is valid for publishing."""
    if not topic or not topic.strip():
        return False
    if topic != topic.lower():
        return False
    if not _PUBLISH_TOPIC_RE.fullmatch(topic):
        return False
    segments = topic.split(".")
    return all(_TOPIC_SEGMENT_RE.fullmatch(segment) for segment in segments)


def _validate_subscription_pattern(
    pattern: str,
    *,
    allow_global_wildcard: bool,
) -> str:
    """Validate and normalize a subscription pattern.

    Raises:
        EventBusSubscribeError: When the pattern is invalid.
    """
    if not pattern or not pattern.strip():
        raise EventBusSubscribeError(
            "subscription pattern must be non-empty",
            code=ERROR_SUBSCRIBE_INVALID_PATTERN,
        )
    normalized = pattern.strip()
    if normalized != normalized.lower():
        raise EventBusSubscribeError(
            f"subscription pattern must be lowercase: {normalized!r}",
            code=ERROR_SUBSCRIBE_INVALID_PATTERN,
        )
    if not _PATTERN_RE.fullmatch(normalized):
        raise EventBusSubscribeError(
            f"invalid subscription pattern: {normalized!r}",
            code=ERROR_SUBSCRIBE_INVALID_PATTERN,
        )
    if normalized == "*":
        if not allow_global_wildcard:
            raise EventBusSubscribeError(
                "global wildcard subscription is disabled by policy",
                code=ERROR_SUBSCRIBE_INVALID_PATTERN,
            )
        return normalized
    if normalized.endswith(".*"):
        prefix = normalized[:-2]
        if not prefix or not _is_valid_publish_topic(prefix):
            raise EventBusSubscribeError(
                f"invalid wildcard prefix in pattern: {normalized!r}",
                code=ERROR_SUBSCRIBE_INVALID_PATTERN,
            )
        return normalized
    if not _is_valid_publish_topic(normalized):
        raise EventBusSubscribeError(
            f"invalid exact subscription pattern: {normalized!r}",
            code=ERROR_SUBSCRIBE_INVALID_PATTERN,
        )
    return normalized


def _topic_matches(pattern: str, topic: str) -> bool:
    """Return whether an event topic matches a subscription pattern."""
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-1]
        return topic.startswith(prefix) and len(topic) > len(prefix)
    return pattern == topic


__all__ = [
    "EVENT_BUS_VERSION",
    "EVENT_ENVELOPE_SCHEMA_VERSION",
    "DispatchMode",
    "EventBus",
    "EventBusConfigurationError",
    "EventBusError",
    "EventBusPolicy",
    "EventBusPublishError",
    "EventBusSubscribeError",
    "EventEnvelope",
    "EventHandler",
    "EventTopics",
    "MetricsRecorder",
    "QueueFullPolicy",
    "SubscriberFailure",
    "SubscriptionHandle",
    "SubscriptionState",
]
