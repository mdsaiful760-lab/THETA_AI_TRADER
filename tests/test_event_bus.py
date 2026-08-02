"""Unit tests for core.event_bus."""

from __future__ import annotations

import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from typing import Any

import pytest

from core.event_bus import (
    ERROR_DISPATCH_RECURSION_LIMIT,
    ERROR_PUBLISH_BUS_SHUTDOWN,
    ERROR_PUBLISH_INVALID_CORRELATION_ID,
    ERROR_PUBLISH_INVALID_TOPIC,
    ERROR_PUBLISH_MISSING_PAYLOAD,
    ERROR_PUBLISH_NAIVE_TIMESTAMP,
    ERROR_SUBSCRIBE_INVALID_PATTERN,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    DispatchMode,
    EventBus,
    EventBusConfigurationError,
    EventBusPolicy,
    EventBusPublishError,
    EventBusSubscribeError,
    EventEnvelope,
    SubscriberFailure,
    SubscriptionState,
)
from core.event_topics import EventTopics

UTC = timezone.utc


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def make_envelope(**kwargs: Any) -> EventEnvelope:
    """Build a valid event envelope for tests."""
    topic = kwargs.pop("topic", EventTopics.MARKET_SNAPSHOT_PUBLISHED)
    payload = kwargs.pop("payload", "payload")
    correlation_id = kwargs.pop("correlation_id", "corr-1")
    event_id = kwargs.pop("event_id", "evt-1")
    now = utc_now()
    return EventEnvelope(
        event_id=event_id,
        topic=topic,
        payload=payload,
        correlation_id=correlation_id,
        producer="test_producer",
        occurred_at=now,
        published_at=now,
        **kwargs,
    )


def test_policy() -> None:
    """Default policy uses SYNC dispatch."""
    policy = EventBusPolicy()
    assert policy.dispatch_mode is DispatchMode.SYNC
    assert policy.max_recursion_depth == 3
    assert policy.allow_clear is False


def test_policy_rejects_invalid_queue_size() -> None:
    """Invalid queue size raises configuration error."""
    with pytest.raises(EventBusConfigurationError):
        EventBusPolicy(queue_max_size=0)


def test_policy_rejects_unsupported_dispatch_mode() -> None:
    """Async dispatch mode is not implemented in v1."""
    with pytest.raises(EventBusConfigurationError):
        EventBusPolicy(dispatch_mode=DispatchMode.ASYNC_BOUNDED)


def test_policy_rejects_negative_recursion_depth() -> None:
    """Negative recursion depth raises configuration error."""
    with pytest.raises(EventBusConfigurationError):
        EventBusPolicy(max_recursion_depth=-1)


class TestPublishValidation:
    """Publish validation tests."""

    def test_rejects_empty_topic(self) -> None:
        bus = EventBus()
        with pytest.raises(EventBusPublishError) as exc_info:
            bus.publish(make_envelope(topic=""))
        assert exc_info.value.code == ERROR_PUBLISH_INVALID_TOPIC

    def test_rejects_uppercase_topic(self) -> None:
        bus = EventBus()
        with pytest.raises(EventBusPublishError) as exc_info:
            bus.publish(make_envelope(topic="Market.Snapshot.Published"))
        assert exc_info.value.code == ERROR_PUBLISH_INVALID_TOPIC

    def test_rejects_empty_correlation_id(self) -> None:
        bus = EventBus()
        with pytest.raises(EventBusPublishError) as exc_info:
            bus.publish(make_envelope(correlation_id="   "))
        assert exc_info.value.code == ERROR_PUBLISH_INVALID_CORRELATION_ID

    def test_rejects_none_payload(self) -> None:
        bus = EventBus()
        with pytest.raises(EventBusPublishError) as exc_info:
            bus.publish(make_envelope(payload=None))
        assert exc_info.value.code == ERROR_PUBLISH_MISSING_PAYLOAD

    def test_rejects_naive_occurred_at(self) -> None:
        bus = EventBus()
        naive = datetime(2026, 8, 3, 10, 0, 0)
        envelope = EventEnvelope(
            event_id="evt-naive",
            topic=EventTopics.SYSTEM_HEALTH,
            payload="ok",
            correlation_id="corr-1",
            producer="test",
            occurred_at=naive,
            published_at=utc_now(),
        )
        with pytest.raises(EventBusPublishError) as exc_info:
            bus.publish(envelope)
        assert exc_info.value.code == ERROR_PUBLISH_NAIVE_TIMESTAMP

    def test_rejects_naive_published_at(self) -> None:
        bus = EventBus()
        now = utc_now()
        envelope = EventEnvelope(
            event_id="evt-naive-pub",
            topic=EventTopics.SYSTEM_HEALTH,
            payload="ok",
            correlation_id="corr-1",
            producer="test",
            occurred_at=now,
            published_at=datetime(2026, 8, 3, 10, 0, 0),
        )
        with pytest.raises(EventBusPublishError) as exc_info:
            bus.publish(envelope)
        assert exc_info.value.code == ERROR_PUBLISH_NAIVE_TIMESTAMP

    def test_rejects_after_shutdown(self) -> None:
        bus = EventBus()
        bus.shutdown()
        with pytest.raises(EventBusPublishError) as exc_info:
            bus.publish(make_envelope())
        assert exc_info.value.code == ERROR_PUBLISH_BUS_SHUTDOWN

    def test_convenience_publish_requires_payload(self) -> None:
        bus = EventBus()
        with pytest.raises(EventBusPublishError) as exc_info:
            bus.publish(
                EventTopics.MARKET_SNAPSHOT_PUBLISHED,
                correlation_id="corr-1",
                producer="test",
            )
        assert exc_info.value.code == ERROR_PUBLISH_MISSING_PAYLOAD

    def test_convenience_publish_success(self) -> None:
        bus = EventBus()
        received: list[EventEnvelope] = []

        def handler(event: EventEnvelope) -> None:
            received.append(event)

        bus.subscribe(EventTopics.MARKET_SNAPSHOT_PUBLISHED, handler)
        event_id = bus.publish(
            EventTopics.MARKET_SNAPSHOT_PUBLISHED,
            {"value": 1},
            correlation_id="corr-1",
            producer="test",
            occurred_at=utc_now(),
        )
        assert event_id
        assert len(received) == 1
        assert received[0].payload == {"value": 1}


class TestSubscribeLifecycle:
    """Subscription lifecycle tests."""

    def test_subscribe_pause_resume_unsubscribe(self) -> None:
        bus = EventBus()
        calls: list[str] = []

        def handler(event: EventEnvelope) -> None:
            calls.append(event.event_id)

        handle = bus.subscribe(EventTopics.MARKET_SNAPSHOT_PUBLISHED, handler)
        assert handle.state is SubscriptionState.ACTIVE
        assert bus.subscriber_count() == 1

        bus.publish(make_envelope(event_id="evt-a"))
        assert calls == ["evt-a"]

        bus.pause(handle)
        bus.publish(make_envelope(event_id="evt-b"))
        assert calls == ["evt-a"]

        bus.resume(handle)
        bus.publish(make_envelope(event_id="evt-c"))
        assert calls == ["evt-a", "evt-c"]

        bus.unsubscribe(handle)
        assert bus.subscriber_count() == 0
        bus.publish(make_envelope(event_id="evt-d"))
        assert calls == ["evt-a", "evt-c"]

    def test_unsubscribe_is_idempotent(self) -> None:
        bus = EventBus()
        handle = bus.subscribe(EventTopics.SYSTEM_HEALTH, lambda _e: None)
        bus.unsubscribe(handle)
        bus.unsubscribe(handle)

    def test_invalid_subscription_pattern(self) -> None:
        bus = EventBus()
        with pytest.raises(EventBusSubscribeError) as exc_info:
            bus.subscribe("invalid*pattern", lambda _e: None)
        assert exc_info.value.code == ERROR_SUBSCRIBE_INVALID_PATTERN

    def test_global_wildcard_requires_policy(self) -> None:
        bus = EventBus()
        with pytest.raises(EventBusSubscribeError):
            bus.subscribe("*", lambda _e: None)

    def test_global_wildcard_allowed_by_policy(self) -> None:
        bus = EventBus(
            EventBusPolicy(allow_global_wildcard=True, allow_clear=True)
        )
        received: list[str] = []
        bus.subscribe("*", lambda event: received.append(event.topic))
        bus.publish(make_envelope(topic=EventTopics.SYSTEM_HEALTH))
        bus.publish(make_envelope(topic=EventTopics.MARKET_SNAPSHOT_PUBLISHED))
        assert len(received) == 2


    def test_pause_and_resume_unknown_handle_is_noop(self) -> None:
        bus = EventBus()
        handle = bus.subscribe(EventTopics.SYSTEM_HEALTH, lambda _e: None)
        bus.unsubscribe(handle)
        bus.pause(handle)
        bus.resume(handle)


class TestRouting:
    """Topic routing tests."""

    def test_exact_routing(self) -> None:
        bus = EventBus()
        matched: list[str] = []
        other: list[str] = []

        bus.subscribe(
            EventTopics.MARKET_SNAPSHOT_PUBLISHED,
            lambda event: matched.append(event.event_id),
        )
        bus.subscribe(
            EventTopics.MARKET_SNAPSHOT_SKIPPED,
            lambda event: other.append(event.event_id),
        )

        bus.publish(make_envelope(event_id="only-published"))
        assert matched == ["only-published"]
        assert other == []

    def test_wildcard_prefix_routing(self) -> None:
        bus = EventBus()
        received: list[str] = []

        bus.subscribe("market.snapshot.*", lambda event: received.append(event.topic))
        bus.publish(make_envelope(topic=EventTopics.MARKET_SNAPSHOT_PUBLISHED))
        bus.publish(make_envelope(topic=EventTopics.MARKET_SNAPSHOT_SKIPPED))
        bus.publish(make_envelope(topic=EventTopics.SYSTEM_HEALTH))

        assert received == [
            EventTopics.MARKET_SNAPSHOT_PUBLISHED,
            EventTopics.MARKET_SNAPSHOT_SKIPPED,
        ]

    def test_domain_wildcard_routing(self) -> None:
        bus = EventBus()
        received: list[str] = []

        bus.subscribe("market.*", lambda event: received.append(event.topic))
        bus.publish(make_envelope(topic=EventTopics.MARKET_SNAPSHOT_PUBLISHED))
        bus.publish(make_envelope(topic=EventTopics.ENGINE_RUN_COMPLETED))

        assert received == [EventTopics.MARKET_SNAPSHOT_PUBLISHED]

    def test_subscriber_count_by_topic(self) -> None:
        bus = EventBus()
        bus.subscribe("market.*", lambda _e: None)
        bus.subscribe(EventTopics.MARKET_SNAPSHOT_PUBLISHED, lambda _e: None)
        assert bus.subscriber_count(EventTopics.MARKET_SNAPSHOT_PUBLISHED) == 2
        assert bus.subscriber_count(EventTopics.SYSTEM_HEALTH) == 0


class TestDispatchOrdering:
    """Priority and deterministic ordering tests."""

    def test_priority_ordering(self) -> None:
        bus = EventBus()
        order: list[int] = []

        bus.subscribe(
            EventTopics.SYSTEM_HEALTH,
            lambda _e: order.append(0),
            priority=0,
        )
        bus.subscribe(
            EventTopics.SYSTEM_HEALTH,
            lambda _e: order.append(100),
            priority=100,
        )
        bus.subscribe(
            EventTopics.SYSTEM_HEALTH,
            lambda _e: order.append(50),
            priority=50,
        )

        bus.publish(make_envelope(topic=EventTopics.SYSTEM_HEALTH))
        assert order == [100, 50, 0]

    def test_deterministic_fifo_tiebreak(self) -> None:
        bus = EventBus()
        order: list[str] = []

        bus.subscribe(
            EventTopics.SYSTEM_HEALTH,
            lambda _e: order.append("first"),
            priority=0,
        )
        bus.subscribe(
            EventTopics.SYSTEM_HEALTH,
            lambda _e: order.append("second"),
            priority=0,
        )

        for _ in range(3):
            order.clear()
            bus.publish(make_envelope(topic=EventTopics.SYSTEM_HEALTH))
            assert order == ["first", "second"]


class TestErrorIsolation:
    """Handler failure isolation tests."""

    def test_failing_handler_does_not_block_others(self) -> None:
        bus = EventBus()
        seen: list[str] = []

        def failing(_event: EventEnvelope) -> None:
            raise RuntimeError("boom")

        bus.subscribe(
            EventTopics.SYSTEM_HEALTH,
            failing,
            priority=100,
        )
        bus.subscribe(
            EventTopics.SYSTEM_HEALTH,
            lambda _e: seen.append("ok"),
            priority=0,
        )

        bus.publish(make_envelope(topic=EventTopics.SYSTEM_HEALTH))
        assert seen == ["ok"]

    def test_diagnostic_event_emitted_on_failure(self) -> None:
        bus = EventBus()
        diagnostics: list[SubscriberFailure] = []

        bus.subscribe(
            EventTopics.SUBSCRIBER_FAILED,
            lambda event: diagnostics.append(event.payload),  # type: ignore[arg-type]
        )

        def failing(_event: EventEnvelope) -> None:
            raise ValueError("handler failed")

        bus.subscribe(EventTopics.SYSTEM_HEALTH, failing)
        bus.publish(make_envelope(topic=EventTopics.SYSTEM_HEALTH))

        assert len(diagnostics) == 1
        failure = diagnostics[0]
        assert isinstance(failure, SubscriberFailure)
        assert failure.error_type == "ValueError"
        assert "handler failed" in failure.message


    def test_diagnostic_events_disabled_by_policy(self) -> None:
        bus = EventBus(
            EventBusPolicy(
                emit_subscriber_failure_events=False,
                allow_global_wildcard=True,
            )
        )
        diagnostics: list[SubscriberFailure] = []
        bus.subscribe(
            EventTopics.SUBSCRIBER_FAILED,
            lambda event: diagnostics.append(event.payload),  # type: ignore[arg-type]
        )

        def failing(_event: EventEnvelope) -> None:
            raise RuntimeError("no diagnostic")

        bus.subscribe(EventTopics.SYSTEM_HEALTH, failing)
        bus.publish(make_envelope(topic=EventTopics.SYSTEM_HEALTH))
        assert diagnostics == []


class TestRecursionProtection:
    """Recursive publish depth tests."""

    def test_recursion_limit_blocks_deep_publish(self) -> None:
        bus = EventBus(
            EventBusPolicy(max_recursion_depth=2, allow_global_wildcard=True)
        )
        depth = {"max": 0}
        blocked = {"value": False}

        def recursive_handler(_event: EventEnvelope) -> None:
            depth["max"] += 1
            try:
                bus.publish(
                    EventTopics.SYSTEM_HEALTH,
                    "nested",
                    correlation_id="corr-1",
                    producer="recursive",
                    occurred_at=utc_now(),
                )
            except EventBusPublishError as exc:
                blocked["value"] = exc.code == ERROR_DISPATCH_RECURSION_LIMIT

        bus.subscribe("*", recursive_handler)
        bus.publish(
            EventTopics.SYSTEM_HEALTH,
            "root",
            correlation_id="corr-1",
            producer="test",
            occurred_at=utc_now(),
        )
        assert depth["max"] >= 1
        assert blocked["value"] is True


class TestShutdownAndClear:
    """Shutdown and test-only clear behavior."""

    def test_clear_requires_policy(self) -> None:
        bus = EventBus()
        with pytest.raises(EventBusConfigurationError):
            bus.clear()

    def test_clear_removes_subscriptions(self) -> None:
        bus = EventBus(EventBusPolicy(allow_clear=True))
        bus.subscribe(EventTopics.SYSTEM_HEALTH, lambda _e: None)
        assert bus.subscriber_count() == 1
        bus.clear()
        assert bus.subscriber_count() == 0


class TestImmutability:
    """Immutable model tests."""

    def test_event_envelope_is_frozen(self) -> None:
        envelope = make_envelope()
        with pytest.raises(FrozenInstanceError):
            envelope.topic = "changed"  # type: ignore[misc]


class TestThreadSafety:
    """Concurrent publish and subscribe tests."""

    def test_concurrent_publish_and_subscribe(self) -> None:
        policy = EventBusPolicy(allow_global_wildcard=True)
        bus = EventBus(policy)
        lock = threading.Lock()
        count = {"value": 0}

        def handler(_event: EventEnvelope) -> None:
            with lock:
                count["value"] += 1

        bus.subscribe("*", handler)

        def worker(index: int) -> None:
            if index % 3 == 0:
                bus.subscribe(
                    EventTopics.SYSTEM_HEALTH,
                    lambda _e: None,
                )
            bus.publish(
                EventTopics.SYSTEM_HEALTH,
                index,
                correlation_id=f"corr-{index}",
                producer="stress",
                occurred_at=utc_now(),
            )

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(worker, i) for i in range(200)]
            for future in as_completed(futures):
                future.result()

        assert count["value"] >= 200


class TestMetricsRecorder:
    """Metrics hook tests."""

    def test_metrics_recorded(self) -> None:
        recorder = CapturingMetrics()
        bus = EventBus(metrics_recorder=recorder)
        bus.subscribe(EventTopics.SYSTEM_HEALTH, lambda _e: None)
        bus.publish(make_envelope(topic=EventTopics.SYSTEM_HEALTH))

        assert ("event_bus_publish_total", EventTopics.SYSTEM_HEALTH) in recorder.counters
        assert recorder.gauges.get("event_bus_active_subscriptions") == 1.0


class TestPerformanceSmoke:
    """Performance smoke tests."""

    def test_twenty_noop_subscribers_under_budget(self) -> None:
        bus = EventBus()
        for _ in range(20):
            bus.subscribe(EventTopics.MARKET_SNAPSHOT_PUBLISHED, lambda _e: None)

        durations: list[float] = []
        for _ in range(50):
            start = time.perf_counter()
            bus.publish(make_envelope())
            durations.append(time.perf_counter() - start)

        median_ms = statistics.median(durations) * 1000.0
        assert median_ms < 10.0


class TestEventTopics:
    """Canonical topic constant tests."""

    def test_market_snapshot_topic_constant(self) -> None:
        assert EventTopics.MARKET_SNAPSHOT_PUBLISHED == "market.snapshot.published"

    def test_subscriber_failed_topic_constant(self) -> None:
        assert EventTopics.SUBSCRIBER_FAILED == "event_bus.subscriber.failed"


class TestEnvelopeSchema:
    """Envelope metadata tests."""

    def test_default_schema_version(self) -> None:
        envelope = make_envelope()
        assert envelope.schema_version == EVENT_ENVELOPE_SCHEMA_VERSION


class CapturingMetrics:
    """Test double that records metric calls."""

    def __init__(self) -> None:
        self.counters: list[tuple[str, str | None]] = []
        self.histograms: list[tuple[str, float]] = []
        self.gauges: dict[str, float] = {}

    def increment_counter(
        self,
        name: str,
        *,
        labels: dict[str, str] | None = None,
        value: float = 1.0,
    ) -> None:
        topic = labels.get("topic") if labels else None
        self.counters.append((name, topic))

    def observe_histogram(
        self,
        name: str,
        value: float,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        self.histograms.append((name, value))

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        self.gauges[name] = value
