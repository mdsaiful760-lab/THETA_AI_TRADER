# Event Bus — Software Engineering Specification

| Field | Value |
|---|---|
| **Module** | `core/event_bus.py` |
| **Document version** | 1.0.0 |
| **Status** | Draft — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-02 |

---

## 1. Purpose

`core/event_bus.py` defines the **communication backbone** of THETA AI TRADER.

The platform architecture requires that intelligence engines remain **stateless, independent, and non-coupled** — they must not call one another directly. Instead, producers publish immutable events and consumers subscribe to typed topics. The event bus is the single in-process facility that makes this decoupling enforceable, testable, and extensible.

Today, ad-hoc callback lists appear in planned modules (e.g., `SnapshotPublisher` in `market_data_engine.py`) and legacy pipelines invoke engines through direct function calls. That pattern does not scale as engines multiply: every new consumer forces edits to producer code, error handling is inconsistent, and cross-cutting concerns (correlation, metrics, logging) are duplicated.

The event bus resolves this by providing:

1. A **uniform publish/subscribe contract** for all platform events.
2. **Topic-based routing** so new engines subscribe without modifying publishers.
3. **Thread-safe, high-performance dispatch** suitable for live market data cadence.
4. **Structured event envelopes** with correlation, provenance, and timestamps for auditability.
5. **Isolated subscriber failure handling** so one faulty handler cannot halt the pipeline.

### Goals

1. Enable **open extension** — add a new engine or service by subscribing to existing topics; no changes to `core/event_bus.py` required for domain events defined elsewhere.
2. Enforce **engine independence** — analytical engines publish `EngineResult` events; orchestrators subscribe and route; engines never import peer engines.
3. Integrate cleanly with **`MarketSnapshot`** publications from the market data engine and **`EngineContext` / `EngineResult`** from the base engine contract.
4. Remain **infrastructure only** — no broker, trading, intelligence, or UI logic.
5. Support deterministic unit testing via an in-memory bus with synchronous dispatch.

### Success criteria

- Market data engine publishes snapshot events through the bus instead of bespoke subscriber lists.
- Orchestrator subscribes to `market.snapshot.published` and triggers downstream engine runs without coupling to market data internals.
- A new engine can be added to the platform by registering a subscriber in its module bootstrap — zero edits to existing publishers.
- Publish dispatch median < 0.1 ms for 20 subscribers on reference hardware (excluding subscriber work).
- All events traceable by `correlation_id` across the pipeline.

### Pipeline placement

```text
[Market Data Engine]
    publish → market.snapshot.published
              ↓
         [Event Bus]
              ↓ subscribe
[Orchestrator] ──→ builds EngineContext ──→ [Regime Engine.run]
                                              publish → engine.result.completed
                                                    ↓
                                               [Event Bus]
                                                    ↓ subscribe
                                              [Next engine / Risk / Dashboard]
```

### Relationship to other modules

| Module | Relationship |
|---|---|
| `core/base_engine.py` | Engines produce `EngineResult`; orchestrator publishes `engine.result.*` events wrapping results. |
| `core/engine_context.py` | `correlation_id`, `as_of`, `source` mirrored on event envelope. |
| `market_data/market_snapshot.py` | Primary payload for `market.snapshot.*` events. |
| `market_data/market_data_engine.py` | Replaces internal `SnapshotPublisher` with bus publish calls. |
| `market_data/market_data_adapter.py` | No direct dependency; adapter stays upstream of snapshots. |
| Orchestrator (future `core/pipeline_orchestrator.py`) | Primary subscriber and secondary publisher. |
| Execution / broker layers | Must not publish order events through v1 bus without dedicated topic namespace review. |

---

## 2. Responsibilities

`core/event_bus.py` **is responsible for**:

| # | Responsibility | Description |
|---|---|---|
| R1 | **Event envelope model** | Define immutable `EventEnvelope` wrapping topic, payload, metadata, and timestamps. |
| R2 | **Topic registry** | Define canonical topic naming conventions and reserved namespaces. |
| R3 | **Publish API** | Accept events from any producer; validate envelope invariants. |
| R4 | **Subscribe API** | Register and unregister handlers by topic pattern. |
| R5 | **Subscription lifecycle** | Issue handles; support pause, resume, unsubscribe. |
| R6 | **Event routing** | Deliver each event to all matching subscribers deterministically. |
| R7 | **Thread-safe dispatch** | Safe concurrent publish and subscribe from multiple threads. |
| R8 | **Subscriber error isolation** | Catch handler exceptions; emit diagnostic events; never abort other subscribers. |
| R9 | **Dispatch policy** | Synchronous in-process dispatch in v1; optional bounded async queue. |
| R10 | **Correlation propagation** | Require/propagate `correlation_id` on envelopes for tracing. |
| R11 | **Metrics hooks** | Count publishes, deliveries, failures, latency. |
| R12 | **Logging conventions** | Standard log events for publish, subscribe, dispatch, handler failure. |
| R13 | **Error taxonomy** | Stable codes under `EVENT_BUS.*`. |
| R14 | **Testing support** | `InMemoryEventBus` (or configurable sync bus) for deterministic tests. |
| R15 | **Documentation contract** | Google-style docstrings; topic catalog in appendix. |

---

## 3. Non-Responsibilities

`core/event_bus.py` **must not**:

| # | Non-responsibility | Rationale |
|---|---|---|
| NR1 | **Orchestrate pipelines** | Sequencing engines belongs in orchestrator; bus only delivers messages. |
| NR2 | **Invoke `BaseEngine.run` directly** | Subscribers decide whether to run engines. |
| NR3 | **Implement broker connectivity** | Market data engine responsibility. |
| NR4 | **Normalize market data** | Adapter responsibility. |
| NR5 | **Generate trading signals or orders** | Decision and execution layers. |
| NR6 | **Perform market intelligence** | Analytical engines. |
| NR7 | **Risk scoring or position sizing** | Risk engines. |
| NR8 | **Persist events to disk or external message brokers** | v1 is in-process only; Kafka/Redis are future adapters. |
| NR9 | **Render UI or dashboards** | UI subscribes as a consumer; bus has no UI knowledge. |
| NR10 | **Load configuration files or environment variables** | Accept `EventBusPolicy` at construction. |
| NR11 | **Define domain payload schemas** | Domain modules define payload types; bus carries opaque immutable references. |
| NR12 | **Guarantee exactly-once delivery across processes** | In-process at-most-once synchronous delivery in v1. |
| NR13 | **Authenticate users or brokers** | Security layer external. |

---

## 4. Architecture

### 4.1 Component model

```text
┌──────────────────────────────────────────────────────────────┐
│                         EventBus                              │
│  ┌────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │ TopicRouter    │  │ Subscription    │  │ DispatchEngine  │ │
│  │ (trie/index)   │  │ Registry        │  │ (sync / queued) │ │
│  └───────┬────────┘  └────────┬────────┘  └────────┬────────┘ │
│          │                    │                     │          │
│          └────────────────────┴─────────────────────┘          │
│                              │                                  │
│                    EventEnvelope (immutable)                    │
└──────────────────────────────────────────────────────────────┘
         ▲ publish                              │ deliver
         │                                     ▼
   Producers                             Subscribers
 (engines, data plane, orchestrator)    (engines, orchestrator, metrics)
```

### 4.2 Design principles

| Principle | Application |
|---|---|
| **Open/Closed** | New topics and subscribers added without modifying bus core. |
| **Immutability** | Envelopes and payloads are immutable after publish. |
| **Fail isolated** | Subscriber errors contained; bus stays available. |
| **Explicit routing** | String topics with documented hierarchy; no implicit globals. |
| **Stateless dispatch** | Bus holds subscription registry state only; no domain accumulation. |
| **Thin infrastructure** | Bus does not interpret payloads beyond type metadata optional field. |

### 4.3 Dependency direction

```text
domain engines / market_data  →  core/event_bus.py  →  stdlib only
orchestrator                  →  core/event_bus.py
```

No reverse imports from `core/event_bus.py` into domain engines.

### 4.4 v1 scope boundary

- **In-process, single JVM/Python interpreter** delivery.
- **Synchronous default dispatch** on publisher thread (or dedicated dispatcher thread if policy enabled).
- **No distributed messaging** in v1.

---

## 5. Event Model

### 5.1 `EventEnvelope` (immutable dataclass)

| Field | Required | Type | Description |
|---|---|---|---|
| `event_id` | Yes | `str` | Unique event identifier (UUID v4). |
| `topic` | Yes | `str` | Hierarchical topic string (see §7). |
| `payload` | Yes | `object` | Immutable domain object or frozen dataclass. |
| `correlation_id` | Yes | `str` | Ties event to pipeline run. |
| `causation_id` | No | `str` | Prior `event_id` that caused this event. |
| `producer` | Yes | `str` | Publishing component name (e.g., `"market_data_engine"`). |
| `producer_version` | No | `str` | Semantic version of producer. |
| `occurred_at` | Yes | timezone-aware `datetime` | When the domain fact occurred. |
| `published_at` | Yes | timezone-aware `datetime` | When bus accepted the event. |
| `schema_version` | Yes | `str` | Envelope schema semver (`"1.0.0"`). |
| `payload_type` | No | `str` | Fully-qualified type name for diagnostics. |
| `tags` | No | `Mapping[str, str]` | Immutable key-value labels. |

### 5.2 Invariants

1. Envelopes are immutable after construction (`frozen=True`).
2. `topic` is non-empty, lowercase, dot-separated (see §7.1).
3. `correlation_id` is non-empty after strip.
4. Timestamps must be timezone-aware.
5. `payload` must not be mutated after publish; producers must pass immutable objects.
6. `event_id` is unique within process lifetime (UUID collision ignored as impossible).

### 5.3 Event categories

| Category | Topic prefix | Examples |
|---|---|---|
| Market data | `market.` | `market.snapshot.published` |
| Engine lifecycle | `engine.` | `engine.run.completed` |
| Pipeline | `pipeline.` | `pipeline.run.started` |
| System | `system.` | `system.health`, `system.error` |
| Bus internal | `event_bus.` | `event_bus.subscriber.failed` |

Domain teams may add new topics under existing prefixes without changing bus code.

### 5.4 Relationship to `EngineContext` / `EngineResult`

| Bus field | Engine equivalent |
|---|---|
| `correlation_id` | `EngineContext.correlation_id` |
| `occurred_at` | `EngineContext.as_of` / `EngineMetadata` timestamp |
| `producer` | `EngineMetadata.engine_name` |
| `payload` | `EngineContext.payload` or `EngineResult` |

Orchestrator maps events to engine inputs; bus does not perform mapping.

---

## 6. Event Types

### 6.1 Canonical topics (v1)

| Topic | Producer | Payload type (domain) | Description |
|---|---|---|---|
| `market.snapshot.published` | Market data engine | `MarketSnapshot` | Valid snapshot available for downstream use. |
| `market.snapshot.skipped` | Market data engine | `PublishSkipReason` * | Publish intentionally skipped (degraded, coverage). |
| `market.snapshot.failed` | Market data engine | `PublishFailureReason` * | Assembly/adapter failure. |
| `engine.run.started` | Orchestrator | `EngineRunStarted` * | Engine run initiated. |
| `engine.run.completed` | Orchestrator or engine wrapper | `EngineResult` | Engine finished with result. |
| `engine.run.rejected` | Orchestrator or engine wrapper | `EngineResult` | Validation rejected (`REJECTED` status). |
| `engine.run.failed` | Orchestrator or engine wrapper | `EngineResult` | Execution failed. |
| `pipeline.run.started` | Orchestrator | `PipelineRunStarted` * | Full pipeline cycle started. |
| `pipeline.run.completed` | Orchestrator | `PipelineRunCompleted` * | Pipeline cycle finished. |
| `system.health` | Any | `HealthStatus` * | Periodic health beacon. |
| `system.error` | Any | `SystemErrorRecord` * | Non-fatal platform error. |
| `event_bus.subscriber.failed` | Event bus | `SubscriberFailure` * | Handler exception diagnostics. |

\* Supporting payload dataclasses defined in respective domain modules or `core/event_bus.py` for bus-internal types only.

### 6.2 Topic naming rules

Format:

```text
<domain>.<entity>.<action>
```

Rules:

- Lowercase ASCII letters, digits, dots, underscores.
- Minimum three segments for domain events: `market.snapshot.published`.
- Bus reserved prefix: `event_bus.`.
- Wildcard subscription uses `*` only as final segment (see §9).

### 6.3 Versioning payload schemas

- Payload schema evolution is owned by payload modules (`market_snapshot`, `engine_result`).
- Envelope `schema_version` bumps only when envelope fields change.
- Subscribers must tolerate unknown optional envelope fields (forward compatibility).

### 6.4 `EventTopic` constants (optional helper class)

Provide frozen constants object or enum-like namespace for v1 canonical topics to avoid string typos — e.g., `EventTopics.MARKET_SNAPSHOT_PUBLISHED`. New topics added as constants in `core/event_topics.py` (separate file) to keep bus core closed for modification.

---

## 7. Publish/Subscribe API

### 7.1 Public types

| Symbol | Kind | Description |
|---|---|---|
| `EVENT_BUS_VERSION` | Constant | `"1.0.0"` |
| `EventBusPolicy` | Frozen dataclass | Dispatch mode, error handling, queue bounds. |
| `EventEnvelope` | Frozen dataclass | Event wrapper. |
| `SubscriptionHandle` | Frozen dataclass | Opaque handle with `id`, `topic`, `state`. |
| `SubscriptionState` | Enum | `ACTIVE`, `PAUSED`, `UNSUBSCRIBED` |
| `EventHandler` | Protocol | `__call__(event: EventEnvelope) -> None` |
| `EventBus` | Class | Primary facade. |
| `EventBusError` | Exception | Base bus error. |
| `EventBusConfigurationError` | Exception | Invalid policy. |
| `EventBusPublishError` | Exception | Invalid envelope at publish. |

### 7.2 `EventBus` methods

| Method | Description |
|---|---|
| `__init__(policy: EventBusPolicy \| None = None)` | Construct bus; default policy if None. |
| `publish(envelope: EventEnvelope) -> str` | Publish event; returns `event_id`. |
| `publish(topic, payload, *, correlation_id, producer, occurred_at, ...) -> str` | Convenience builder + publish. |
| `subscribe(topic: str, handler: EventHandler, *, priority: int = 0) -> SubscriptionHandle` | Register handler for exact topic or pattern. |
| `unsubscribe(handle: SubscriptionHandle) -> None` | Remove handler; idempotent. |
| `pause(handle) -> None` | Pause delivery to handler. |
| `resume(handle) -> None` | Resume paused handler. |
| `subscriber_count(topic: str \| None = None) -> int` | Metrics/introspection. |
| `clear() -> None` | Remove all subscriptions (**test only**; guarded by policy flag). |
| `shutdown() -> None` | Reject new publishes; drain queue if async; test/lifecycle hook. |

### 7.3 Handler contract

- Handlers must be **non-blocking** for live topics (`market.*`); offload heavy work to worker queues inside subscriber.
- Handlers must not call `publish` reentrantly on same thread unless policy allows (default: allow with recursion depth limit 3).
- Handlers receive immutable envelope; must not mutate `payload`.
- Handler exceptions are caught by bus; never propagate to publisher.

### 7.4 Priority

When multiple subscribers match a topic:

1. Sort by `priority` descending (higher first).
2. Tie-break by subscription registration order (FIFO).

Documented for orchestrator (priority 100) vs metrics (priority 0) vs logging tap (priority -100).

### 7.5 Publish validation

Reject publish with `EventBusPublishError` when:

| Rule | Error code |
|---|---|
| Empty topic | `EVENT_BUS.PUBLISH.INVALID_TOPIC` |
| Empty correlation_id | `EVENT_BUS.PUBLISH.INVALID_CORRELATION_ID` |
| Naive timestamp | `EVENT_BUS.PUBLISH.NAIVE_TIMESTAMP` |
| None payload | `EVENT_BUS.PUBLISH.MISSING_PAYLOAD` |
| Bus shutdown | `EVENT_BUS.PUBLISH.BUS_SHUTDOWN` |

---

## 8. Subscription Lifecycle

### 8.1 States

```text
[subscribe()]
    → ACTIVE
[pause()]
    → PAUSED (skipped during dispatch)
[resume()]
    → ACTIVE
[unsubscribe()]
    → UNSUBSCRIBED (removed from registry)
```

### 8.2 `SubscriptionHandle` fields

| Field | Description |
|---|---|
| `subscription_id` | UUID |
| `topic` | Registered topic pattern |
| `state` | `SubscriptionState` |
| `priority` | Dispatch priority |
| `registered_at` | Timezone-aware timestamp |
| `handler_id` | Optional string for debugging (`handler.__qualname__`) |

### 8.3 Lifecycle rules

- `unsubscribe` on unknown or already unsubscribed handle is no-op.
- Paused handlers remain in registry but are skipped during dispatch.
- Bus holds **weak references** to handlers optional extension; v1 strong references for simplicity (document memory implication).
- Subscriptions are **not** persisted; process restart requires re-subscription.

### 8.3 Thread-safe registration

Subscribe/unsubscribe/pause/resume use same registry lock as dispatch snapshot.

---

## 9. Event Routing

### 9.1 Matching rules (v1)

| Pattern | Matches |
|---|---|
| Exact `market.snapshot.published` | Only that topic |
| Prefix `market.snapshot.*` | Any topic with prefix `market.snapshot.` |
| Prefix `market.*` | All market domain topics |
| Global `*` | All topics (**test/metrics only**; restricted by policy) |

`*` is permitted only as the final character of pattern after a dot or as sole `*`.

Invalid patterns rejected at subscribe time.

### 9.2 Routing algorithm

```text
publish(envelope):
    1. validate envelope
    2. snapshot matching subscriptions under lock (copy list)
    3. sort by priority, registration order
    4. for each subscription:
         if PAUSED: continue
         invoke handler(envelope) with error isolation
    5. record metrics
```

Complexity: O(S) where S = number of matching subscribers.

### 9.3 Determinism

Given same subscription registration order and same publish sequence, delivery order is deterministic.

### 9.4 No content-based routing in v1

Routing is **topic-only**. Payload inspection for routing belongs in subscribers or future rules engine extension.

---

## 10. Thread Safety

| Aspect | Requirement |
|---|---|
| `publish` | Safe concurrent calls from multiple threads |
| `subscribe` / `unsubscribe` | Safe concurrent with publish |
| Registry | Protected by `threading.RLock` |
| Dispatch snapshot | Copy subscriber list under lock; invoke handlers outside lock |
| `EventEnvelope` | Immutable; safe to share across threads |
| Payload | Must be immutable or treated read-only after publish |
| Global singleton bus | **Forbidden** — inject `EventBus` instance |
| Reentrancy | Recursion depth tracked; exceed limit → `EVENT_BUS.DISPATCH.RECURSION_LIMIT` diagnostic |

---

## 11. Concurrency Model

### 11.1 Dispatch modes (`EventBusPolicy`)

| Mode | Description | Default |
|---|---|---|
| `SYNC` | Publish thread invokes handlers synchronously | **Yes (v1)** |
| `ASYNC_BOUNDED` | Enqueue event; dispatcher thread delivers | Optional |
| `ASYNC_DROP_ON_FULL` | Drop lowest-priority deliveries when queue full | Optional extension |

### 11.2 SYNC mode (v1 default)

- Lowest latency; simplest tests.
- Publisher blocked for duration of all handlers — producers must keep handlers fast.
- Market data engine should use thin subscribers that enqueue work.

### 11.3 ASYNC_BOUNDED mode (optional)

| Parameter | Default |
|---|---|
| `queue_max_size` | `10_000` |
| `dispatcher_threads` | `1` |
| `publish_timeout_seconds` | `0.1` (block or fail if queue full per policy) |

On queue full:

- Policy `BLOCK` — back-pressure publisher (use carefully).
- Policy `DROP_OLDEST` — drop oldest queued event with metric increment.
- Policy `RAISE` — raise `EventBusPublishError`.

### 11.4 Interaction with market data cadence

At 1 Hz snapshot publish with 10 subscribers each < 1 ms, SYNC mode total overhead < 10 ms — acceptable. If subscribers grow, switch orchestrator to ASYNC_BOUNDED.

---

## 12. Error Handling

### 12.1 Error taxonomy

| Code | Description |
|---|---|
| `EVENT_BUS.PUBLISH.INVALID_TOPIC` | Malformed topic |
| `EVENT_BUS.PUBLISH.INVALID_CORRELATION_ID` | Missing correlation |
| `EVENT_BUS.PUBLISH.NAIVE_TIMESTAMP` | Naive datetime |
| `EVENT_BUS.PUBLISH.MISSING_PAYLOAD` | None payload |
| `EVENT_BUS.PUBLISH.BUS_SHUTDOWN` | Publish after shutdown |
| `EVENT_BUS.SUBSCRIBE.INVALID_PATTERN` | Bad topic pattern |
| `EVENT_BUS.SUBSCRIBE.DUPLICATE` | Optional warning if same handler+topic |
| `EVENT_BUS.DISPATCH.HANDLER_FAILED` | Handler raised exception |
| `EVENT_BUS.DISPATCH.RECURSION_LIMIT` | Excessive reentrant publish depth |
| `EVENT_BUS.DISPATCH.TIMEOUT` | Handler exceeded optional timeout budget |

### 12.2 Handler failure policy

On handler exception:

1. Catch and log at ERROR with `event_id`, `topic`, `subscription_id`, handler id.
2. Increment `event_bus_subscriber_failures_total` metric.
3. Publish `event_bus.subscriber.failed` diagnostic event **unless** recursion depth prevents (avoid infinite loop).
4. Continue remaining subscribers.

Handler failure **never** propagates to publisher.

### 12.3 Optional handler timeout (extension)

`EventBusPolicy.handler_timeout_ms` — if set, run handler in executor with timeout; on timeout treat as failure. Default `None` (no timeout) in v1.

---

## 13. Performance Requirements

| Requirement | Target | Notes |
|---|---|---|
| Publish overhead (no subscribers) | < 0.05 ms median | Validation + registry lookup |
| Dispatch per subscriber | < 0.05 ms median | Excluding handler body |
| Publish + 20 subscribers (noop handlers) | < 1 ms median | SYNC mode |
| Subscribe / unsubscribe | < 0.1 ms median | |
| Memory per subscription | ≤ 512 bytes | Excluding handler closure |
| Queue throughput (ASYNC mode) | ≥ 50k events/s | Micro-benchmark; not live requirement |
| Topic registry scale | ≥ 500 subscriptions | Institutional ceiling v1 |

Benchmarks in `tests/test_event_bus.py`.

---

## 14. Metrics

### 14.1 Counters

| Metric | Labels | Description |
|---|---|---|
| `event_bus_publish_total` | `topic`, `producer` | Events published |
| `event_bus_deliveries_total` | `topic`, `subscription_id` | Handler invocations |
| `event_bus_subscriber_failures_total` | `topic`, `handler` | Handler exceptions |
| `event_bus_dropped_total` | `reason` | Queue overflow drops |
| `event_bus_recursion_limit_total` | — | Recursion guard trips |

### 14.2 Histograms / gauges

| Metric | Description |
|---|---|
| `event_bus_dispatch_duration_seconds` | Time to invoke all matching handlers |
| `event_bus_handler_duration_seconds` | Per-handler latency (optional sampling) |
| `event_bus_queue_depth` | ASYNC mode queue size |
| `event_bus_active_subscriptions` | Gauge |

### 14.3 Metrics injection

- Optional `MetricsRecorder` protocol injected at construction.
- No-op default for tests.

---

## 15. Logging

### 15.1 Logger

- Module logger: `core.event_bus`

### 15.2 Required log events

| Event | Level | When |
|---|---|---|
| `event_bus.publish` | DEBUG | Each publish (topic, event_id, correlation_id) |
| `event_bus.subscribe` | INFO | New subscription (topic, handler id) |
| `event_bus.unsubscribe` | INFO | Subscription removed |
| `event_bus.dispatch.start` | DEBUG | Begin dispatch for event |
| `event_bus.dispatch.complete` | DEBUG | End dispatch (duration_ms, count) |
| `event_bus.handler.failed` | ERROR | Handler exception |
| `event_bus.publish.rejected` | WARNING | Validation failure |
| `event_bus.shutdown` | INFO | Bus shutdown |

### 15.3 Content rules

- Do log: topic, event_id, correlation_id, producer, subscription_id, duration_ms, error codes.
- Do not log: full payload bodies at INFO (may contain large snapshots); DEBUG only with size truncation.
- Never log: secrets, tokens, credentials.

---

## 16. Security

| Concern | Requirement |
|---|---|
| Secrets in payloads | Forbidden; bus does not inspect but docs warn producers |
| Subscriber isolation | Exceptions contained; no privilege escalation |
| Global wildcard `*` | Disabled in production policy by default (`allow_global_wildcard=False`) |
| Untrusted handlers | Same process trust model; bus for internal platform only |
| Event injection from external network | Out of scope v1 — no network listener in bus module |
| Denial of service | Queue bounds; recursion limit; optional publish rate limit per producer |
| Payload mutability | Document contract; misuse can cause data races — prefer frozen dataclasses |

---

## 17. Testing Strategy

Tests live in `tests/test_event_bus.py`.

### 17.1 Test doubles

| Double | Purpose |
|---|---|
| `noop_handler` | Count invocations |
| `failing_handler` | Raise controlled exception |
| `slow_handler` | Timeout/async tests |
| `CapturingBus` | Alias for `EventBus` with `clear()` enabled |

### 17.2 Required test cases

| Category | Cases |
|---|---|
| **Publish validation** | Reject invalid topic, correlation, timestamps, shutdown |
| **Subscribe lifecycle** | subscribe, pause, resume, unsubscribe idempotency |
| **Routing exact** | Deliver only matching exact topic |
| **Routing wildcard** | Prefix `*` patterns |
| **Priority ordering** | Higher priority first |
| **Error isolation** | Failing handler does not block others |
| **Diagnostic event** | `event_bus.subscriber.failed` emitted |
| **Thread safety** | Concurrent publish + subscribe stress |
| **Determinism** | Same order across repeated runs |
| **Reentrancy limit** | Recursive publish guarded |
| **Immutability** | Envelope frozen |
| **Integration** | Publish `MarketSnapshot` mock payload; orchestrator-style handler |
| **Performance smoke** | 20 subscribers noop < 1 ms |
| **clear()** | Test-only reset |

### 17.3 Coverage target

≥ 95% line coverage on `core/event_bus.py`.

### 17.4 Integration tests

Optional: `tests/test_event_bus_integration.py` with market data engine publishing to bus (mock engine).

---

## 18. Future Extension Points

| Extension | Description |
|---|---|
| **Distributed bus adapter** | Kafka/NATS/Redis bridge implementing same `EventBus` protocol |
| **Async/await handlers** | Native async dispatch mode |
| **Content-based routing** | Optional filter predicates on envelope tags |
| **Dead letter queue** | Persist failed handler events |
| **Event replay** | Record and replay envelopes for backtest |
| **OpenTelemetry tracing** | Span per publish/dispatch with correlation_id |
| **Schema registry** | Avro/Protobuf payload contracts |
| **Priority queues per topic** | Separate queues for `market.*` vs `engine.*` |
| **Rate limiting** | Per-producer token bucket |
| **Weak handler references** | Auto-unsubscribe dead handlers |

Extensions must preserve immutability and subscriber error isolation.

---

## 19. Definition of Done

The `core/event_bus.py` module and this specification are **done** when:

### 19.1 Implementation

- [ ] All public API symbols in §7 implemented.
- [ ] `EventEnvelope` and `SubscriptionHandle` are immutable.
- [ ] SYNC dispatch mode fully functional; ASYNC optional if scoped.
- [ ] Thread-safe registry and dispatch per §10.
- [ ] Handler error isolation per §12.
- [ ] Stable error codes implemented.
- [ ] No broker, trading, intelligence, order, or UI logic.
- [ ] No environment loading; policy injected at construction.
- [ ] No global singleton bus in production code paths.
- [ ] Google-style docstrings; Python 3.12 type hints.
- [ ] Canonical topics documented in `core/event_topics.py` or appendix constants.

### 19.2 Testing

- [ ] `tests/test_event_bus.py` covers §17.2.
- [ ] Line coverage ≥ 95%.
- [ ] Concurrent stress test passes.
- [ ] Performance smoke passes.

### 19.3 Integration

- [ ] Market data engine (or orchestrator stub) publishes `market.snapshot.published` via bus.
- [ ] Sample orchestrator subscribes and triggers mock engine without direct coupling.
- [ ] `CHANGELOG.md` updated.

### 19.4 Documentation

- [ ] This specification matches implementation.
- [ ] Cross-links added from `market_data_engine.md` (SnapshotPublisher → Event Bus).
- [ ] `base_engine.md` appendix references bus for engine result propagation.

### 19.5 Review checklist

- [ ] Open/Closed — new engine subscribes without bus code changes.
- [ ] Engine independence preserved.
- [ ] Fail isolated subscriber errors.
- [ ] Security — no secrets logged; global wildcard gated.

### 19.6 Sign-off

- [ ] Peer review approved.
- [ ] Specification version bumped if API changed post-review.

---

## Appendix A — Example flows

### A.1 Market snapshot propagation

1. Market data engine calls `bus.publish(topic="market.snapshot.published", payload=snapshot, correlation_id=..., producer="market_data_engine")`.
2. Orchestrator handler receives envelope; builds `EngineContext(as_of=snapshot.provenance.as_of, payload=snapshot)`.
3. Orchestrator runs regime engine; publishes `engine.run.completed` with `EngineResult`.
4. Strategy orchestration handler subscribes to `engine.run.completed` where `producer=="market_regime"`.

### A.2 Adding a new engine (no bus changes)

1. Implement `PortfolioEngine(BaseEngine)`.
2. Register `bus.subscribe("engine.run.completed", on_regime_completed)` in bootstrap if chained off regime.
3. Publish `engine.run.completed` with `producer="portfolio"`.
4. No edits to `core/event_bus.py`.

---

## Appendix B — Supporting payload types (bus-internal)

| Type | Fields | Used for |
|---|---|---|
| `SubscriberFailure` | `subscription_id`, `topic`, `event_id`, `error_type`, `message` | `event_bus.subscriber.failed` |
| `PublishSkipReason` | `reason_code`, `message`, `as_of` | `market.snapshot.skipped` |
| `PublishFailureReason` | `reason_code`, `message`, `as_of` | `market.snapshot.failed` |

Domain payloads (`MarketSnapshot`, `EngineResult`) remain defined in their respective modules.

---

## Appendix C — `EventBusPolicy` fields

| Field | Default | Description |
|---|---|---|
| `dispatch_mode` | `SYNC` | SYNC or ASYNC_BOUNDED |
| `queue_max_size` | `10000` | ASYNC queue capacity |
| `queue_full_policy` | `RAISE` | BLOCK / DROP_OLDEST / RAISE |
| `max_recursion_depth` | `3` | Reentrant publish limit |
| `allow_global_wildcard` | `False` | Allow `*` subscription |
| `allow_clear` | `False` | Enable test-only `clear()` |
| `emit_subscriber_failure_events` | `True` | Diagnostic events on handler failure |
| `handler_timeout_ms` | `None` | Optional per-handler timeout |

---

## Appendix D — Migration from `SnapshotPublisher`

| SnapshotPublisher | Event Bus equivalent |
|---|---|
| `add_subscriber(callback)` | `bus.subscribe("market.snapshot.published", callback)` |
| `remove_subscriber(callback)` | `bus.unsubscribe(handle)` |
| `emit(PublishEvent)` | `bus.publish` with topic by outcome mapping |

Outcome mapping:

| PublishOutcome | Topic |
|---|---|
| PUBLISHED | `market.snapshot.published` |
| SKIPPED | `market.snapshot.skipped` |
| FAILED | `market.snapshot.failed` |

---

## Appendix E — Related documents

- `docs/specifications/base_engine.md`
- `docs/specifications/market_snapshot.md`
- `docs/specifications/market_data_adapter.md`
- `docs/specifications/market_data_engine.md`
- `.cursor/rules/theta-ai-trader-trading-architecture.mdc`
- `.cursor/rules/theta-ai-trader-engineering-standards.mdc`
- `docs/foundation/THETA_AI_TRADER_ARCHITECTURE.md`

---

## Appendix F — Revision history

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0.0 | 2026-08-02 | THETA AI TRADER | Initial specification |
