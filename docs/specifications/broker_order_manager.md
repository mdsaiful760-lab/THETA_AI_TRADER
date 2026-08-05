# Broker Order Manager — Software Engineering Specification

| Field | Value |
|---|---|
| Module | `broker/broker_order_manager.py` |
| Document version | 1.0.0 |
| Status | Implementation contract |
| Owner | THETA AI TRADER Core Platform |
| Last updated | 2026-08-05 |

---

## 1. Purpose

`broker/broker_order_manager.py` is the sole specialised broker-order execution component for THETA AI TRADER v1.0. It owns validated, retried, tracked broker order operations against Zerodha Kite without owning a trading decision.

It answers: once an authorised caller needs a validated place, modify, cancel, status, or execution-tracking operation, who performs that broker operation, classifies retryability, produces immutable results, and reports operational health?

### 1.1 Gap filled

| Component | Boundary |
|---|---|
| `execution/order_manager.py` | Broker-neutral institutional plan lifecycle. Consumes `ExecutionPlan`, maps legs to requests, tracks plan-level `OrderState`, and publishes `order.*` events. |
| `broker/base_broker.py` | Broker-agnostic ABC and shared request/response types. |
| `broker/zerodha/kite_broker.py` | Concrete Kite REST transport. May expose primitive REST methods but does not own this module's validation, retry, health, statistics, or immutable models. |
| This module | Specialised broker-order service and execution tracker behind an injected transport protocol. |

### 1.2 Frozen pipeline

```text
ExecutionEngine → ExecutionPlan → execution/order_manager.py → BaseBrokerClient
```

The pipeline above remains frozen. `ExecutionEngine` never places orders. `BrokerOrderManager` does not replace the Execution Engine or `execution/order_manager.py`.

Production `BaseBrokerClient` implementations may delegate broker-order operations to this service. System Orchestrator, Live Trading Runner, and Paper Trading Runner may call the public service for authorised direct broker operations, polling, and execution tracking. APME receives read-only tracking artifacts only through the orchestrator; it never submits orders directly.

### 1.3 Architecture freeze rules

- **BOUNDARY-BOM-001:** Never evaluate a strategy, calculate an indicator, calculate risk, size a position, manage a portfolio, or decide a trade.
- **BOUNDARY-BOM-002:** Never import `kiteconnect` as the sole execution path. The service depends on `BrokerOrderTransport`.
- **BOUNDARY-BOM-003:** Never load `.env`, credentials, access tokens, or configuration files. Receive a projected immutable config and injected transport.
- **BOUNDARY-BOM-004:** Never call an analytical engine, APME, or strategy module.
- **BOUNDARY-BOM-005:** Never create a long-running trading loop.
- **BOUNDARY-BOM-006:** Never infer order intent from price, market state, or portfolio state.
- **BOUNDARY-BOM-007:** Never treat a transport acknowledgement as a fill.
- **BOUNDARY-BOM-008:** Never expose mutable internal order records.

### 1.4 Goals

1. Provide one explicit execution-operation boundary.
2. Support placement for MARKET, LIMIT, SL, and SL-M orders.
3. Support controlled modification and cancellation.
4. Track broker state, partial fills, average price, quantities, and trade identifiers.
5. Validate every public request before transport.
6. Retry only transient failures under bounded deterministic policy.
7. Expose immutable health and statistics snapshots.
8. Serialize public models as versioned JSON.
9. Remain thread-safe, deterministic, and straightforward to fake in tests.
10. Support Live and Paper transports through the same public interface.

### 1.5 Success criteria

- A valid request creates exactly one logical `BrokerOrderResult` per idempotency key.
- Invalid requests fail before a transport call.
- A rejected order is never retried.
- A timeout after a potentially accepted placement is reconciled by client tag or order lookup before any retry.
- A partial fill produces a monotonic execution snapshot.
- Concurrent readers never observe a partially updated order snapshot.
- Health and statistics are obtainable without mutating order state.
- Unit coverage of `broker/broker_order_manager.py` is at least 95%.

---

## 2. Responsibilities

| ID | Requirement |
|---|---|
| R1 | Place MARKET orders. |
| R2 | Place LIMIT orders. |
| R3 | Place SL orders with a valid limit and trigger price. |
| R4 | Place SL-M orders with a valid trigger price and no limit price. |
| R5 | Modify permitted price, quantity, trigger-price, and validity fields. |
| R6 | Cancel one broker order. |
| R7 | Cancel a batch with independent per-item outcomes. |
| R8 | Fetch and normalize a single order status. |
| R9 | Poll and reconcile open orders. |
| R10 | Track partial fills and final fills. |
| R11 | Track average execution price. |
| R12 | Track filled and remaining quantity. |
| R13 | Deduplicate reported trade identifiers. |
| R14 | Validate instrument identity. |
| R15 | Validate exchange, product, side, type, validity, quantity, and prices. |
| R16 | Classify transport failures. |
| R17 | Retry retryable failures with bounded backoff. |
| R18 | Reconcile ambiguous placement outcomes before replay. |
| R19 | Emit immutable result models. |
| R20 | Maintain health metrics. |
| R21 | Maintain operation statistics. |
| R22 | Support versioned serialization. |
| R23 | Publish optional observational events. |
| R24 | Permit injected clock, sleeper, identifier generator, and transport. |
| R25 | Preserve transport-neutral service semantics. |
| R26 | Keep all shared-state mutation behind a lock. |
| R27 | Permit paper adapters with no special-case public API. |

---

## 3. Non-responsibilities

| ID | Explicit exclusion |
|---|---|
| NR1 | Strategy evaluation |
| NR2 | Signal generation |
| NR3 | Technical indicator calculation |
| NR4 | Market-regime detection |
| NR5 | Risk scoring |
| NR6 | Margin calculation |
| NR7 | Position sizing |
| NR8 | Portfolio management |
| NR9 | Trade approval |
| NR10 | Trade selection |
| NR11 | Execution-plan construction |
| NR12 | Plan-level order-state ownership |
| NR13 | Authentication and token refresh |
| NR14 | Credential storage |
| NR15 | WebSocket market-data streaming |
| NR16 | Instrument-master download |
| NR17 | Direct Kite SDK dependency |
| NR18 | Persistence of audit data outside injected sinks |
| NR19 | Recovering unknown broker state by guessing |
| NR20 | Retrying business rejections |

---

## 4. Supported catalog

### 4.1 Enums

| Enum | Values | Kite wire value |
|---|---|---|
| `OrderSide` | `BUY`, `SELL` | `BUY`, `SELL` |
| `OrderType` | `MARKET`, `LIMIT`, `SL`, `SL_M` | `MARKET`, `LIMIT`, `SL`, `SL-M` |
| `ProductType` | `MIS`, `NRML`, `CNC` | `MIS`, `NRML`, `CNC` |
| `Validity` | `DAY`, `IOC` | `DAY`, `IOC` |
| `Exchange` | `NSE`, `BSE`, `NFO`, `BFO` | same |
| `BrokerOrderStatus` | `PENDING`, `OPEN`, `COMPLETE`, `CANCELLED`, `REJECTED`, `TRIGGER_PENDING` | normalized below |

### 4.2 Product and exchange contract

| Exchange | CASH | DERIVATIVE | Permitted products |
|---|---:|---:|---|
| NSE | Yes | No | MIS, CNC |
| BSE | Yes | No | MIS, CNC |
| NFO | No | Yes | MIS, NRML |
| BFO | No | Yes | MIS, NRML |

The service validates only this platform contract. Broker-specific instrument restrictions remain a transport rejection and are not silently translated into a local policy.

### 4.3 Order-type contract

| Order type | Price | Trigger price | Allowed validity |
|---|---|---|---|
| MARKET | absent | absent | DAY, IOC |
| LIMIT | positive tick-aligned | absent | DAY, IOC |
| SL | positive tick-aligned | positive tick-aligned | DAY, IOC |
| SL_M | absent | positive tick-aligned | DAY, IOC |

---

## 5. Architecture component diagram

```text
              authorised request / observation
 ┌───────────────────────────────────────────────────────────┐
 │ System Orchestrator │ Live Runner │ Paper Runner           │
 └──────────────────────────────┬────────────────────────────┘
                                ▼
 ┌───────────────────────────────────────────────────────────┐
 │ BrokerOrderManager                                         │
 │ validate → idempotency → transport → retry/reconcile       │
 │ normalize → execution ledger → snapshots → metrics/events  │
 └──────────────┬───────────────────────┬────────────────────┘
                ▼                       ▼
      BrokerOrderTransport       immutable BrokerOrder*
                │                       │
     ┌──────────┴─────────┐             └── orchestrator/APME read-only
     ▼                    ▼
 BaseBroker adapter    Paper transport adapter
     ▼                    ▼
 Kite REST             deterministic simulated venue
```

### 5.1 Dependency direction

```text
orchestrator/runners → broker_order_manager → transport protocol → adapter → broker client
execution/order_manager → BaseBrokerClient → production adapter → broker_order_manager
APME → orchestrator execution artifacts
```

**BOUNDARY-BOM-009:** Dependencies may point toward transport abstractions and primitive shared models. They must not point from this module to orchestration, strategy, risk, portfolio, or APME modules.

---

## 6. Configuration

### 6.1 `BrokerOrderManagerConfig`

```python
@dataclass(frozen=True, slots=True)
class BrokerOrderManagerConfig:
    """Immutable operational policy for BrokerOrderManager."""

    max_attempts: int = 3
    initial_retry_delay_seconds: float = 0.25
    max_retry_delay_seconds: float = 2.0
    retry_backoff_multiplier: float = 2.0
    operation_timeout_seconds: float = 8.0
    health_window_size: int = 100
    max_batch_cancel_size: int = 50
    require_client_order_id: bool = True
    serialization_version: int = 1
    reconcile_after_ambiguous_place: bool = True
```

| Rule | Validation |
|---|---|
| CFG-BOM-001 | `max_attempts` is 1 through 5 inclusive. |
| CFG-BOM-002 | Retry delays are finite, non-negative, and maximum is not less than initial. |
| CFG-BOM-003 | Backoff multiplier is at least 1.0. |
| CFG-BOM-004 | Timeout is positive and no more than 60 seconds. |
| CFG-BOM-005 | Health window is 10 through 10,000. |
| CFG-BOM-006 | Batch cancel size is 1 through 100. |
| CFG-BOM-007 | Serialization version is supported. |
| CFG-BOM-008 | Configuration validation has no I/O and is deterministic. |

### 6.2 Profile defaults

| Profile | attempts | initial delay | maximum delay | timeout |
|---|---:|---:|---:|---:|
| Unit test | 1 | 0 | 0 | 1s |
| Paper | 2 | 0.05s | 0.2s | 2s |
| Live conservative | 2 | 0.25s | 0.5s | 5s |
| Live standard | 3 | 0.25s | 2s | 8s |

---

## 7. Public API

### 7.1 Protocols

```python
class BrokerOrderTransport(Protocol):
    """Broker transport boundary used by BrokerOrderManager."""

    def place_order(self, request: PlaceOrderRequest) -> Mapping[str, object]:
        """Submit one broker-native order and return raw normalized fields."""

    def modify_order(self, broker_order_id: str, request: ModifyOrderRequest) -> Mapping[str, object]:
        """Modify one broker-native order and return raw normalized fields."""

    def cancel_order(self, broker_order_id: str, variety: str) -> Mapping[str, object]:
        """Cancel one broker-native order and return raw normalized fields."""

    def fetch_order(self, broker_order_id: str) -> Mapping[str, object]:
        """Fetch one current order representation."""

    def fetch_trades(self, broker_order_id: str) -> Sequence[Mapping[str, object]]:
        """Fetch executions associated with an order."""

    def find_by_client_order_id(self, client_order_id: str) -> Mapping[str, object] | None:
        """Reconcile an ambiguous placement without creating another order."""
```

`find_by_client_order_id` is mandatory for a live transport when placement can time out after request transmission. A paper adapter implements it from its deterministic in-memory ledger.

### 7.2 Request models

The canonical request types remain those in `broker/base_broker.py` where available. This module may expose immutable wrappers that add a mandatory `client_order_id`, instrument metadata, and explicit tick size. It must not fork incompatible side, type, product, validity, or exchange enums.

### 7.3 Output models

| Model | Purpose | Immutable fields |
|---|---|---|
| `BrokerOrder` | Canonical normalized current order snapshot | identity, instrument, requested values, status, timestamps |
| `BrokerOrderResult` | Result of one public operation | operation, order snapshot, attempt count, outcome, error |
| `BrokerExecution` | One deduplicated fill/trade | trade id, order id, quantity, price, timestamp |
| `BrokerOrderStatus` | Normalized lifecycle enum | one stable status value |
| `BrokerHealth` | Operational snapshot | connectivity, latency, failure rate, execution time |
| `BrokerStatistics` | Lifetime counters and aggregates | counts, sums, rates, timestamps |

```python
@dataclass(frozen=True, slots=True)
class BrokerExecution:
    """Immutable broker execution reported for one order."""

    trade_id: str
    broker_order_id: str
    quantity: int
    price: Decimal
    executed_at: datetime
    exchange: Exchange

@dataclass(frozen=True, slots=True)
class BrokerOrder:
    """Immutable normalized broker order snapshot."""

    broker_order_id: str
    client_order_id: str
    instrument_token: int
    trading_symbol: str
    exchange: Exchange
    side: OrderSide
    order_type: OrderType
    product: ProductType
    validity: Validity
    quantity: int
    filled_quantity: int
    remaining_quantity: int
    average_price: Decimal | None
    price: Decimal | None
    trigger_price: Decimal | None
    status: BrokerOrderStatus
    status_message: str | None
    executions: tuple[BrokerExecution, ...]
    created_at: datetime
    updated_at: datetime
    version: int
```

```python
@dataclass(frozen=True, slots=True)
class BrokerOrderResult:
    """Immutable result returned by a broker-order operation."""

    operation: str
    success: bool
    order: BrokerOrder | None
    attempts: int
    broker_request_id: str | None
    error_code: str | None
    error_message: str | None
    completed_at: datetime

@dataclass(frozen=True, slots=True)
class BrokerHealth:
    """Immutable operational health snapshot."""

    connected: bool
    last_success_at: datetime | None
    last_failure_at: datetime | None
    rolling_failure_rate: Decimal
    average_latency_ms: Decimal
    average_execution_time_ms: Decimal
    consecutive_failures: int
    checked_at: datetime

@dataclass(frozen=True, slots=True)
class BrokerStatistics:
    """Immutable lifetime statistics snapshot."""

    placement_attempts: int
    placement_successes: int
    modification_attempts: int
    cancellation_attempts: int
    status_fetches: int
    executions_observed: int
    validation_failures: int
    retry_attempts: int
    transport_failures: int
    rejections: int
    generated_at: datetime
```

### 7.4 Facade

```python
class BrokerOrderManager:
    """Thread-safe façade for validated broker order operations."""

    def place(self, request: PlaceOrderRequest) -> BrokerOrderResult: ...
    def modify(self, broker_order_id: str, request: ModifyOrderRequest) -> BrokerOrderResult: ...
    def cancel(self, broker_order_id: str, *, variety: str = "regular") -> BrokerOrderResult: ...
    def cancel_many(self, broker_order_ids: Sequence[str]) -> tuple[BrokerOrderResult, ...]: ...
    def get_status(self, broker_order_id: str, *, refresh: bool = True) -> BrokerOrderResult: ...
    def track_executions(self, broker_order_id: str) -> BrokerOrderResult: ...
    def get_health(self) -> BrokerHealth: ...
    def get_statistics(self) -> BrokerStatistics: ...
```

Every public method raises only documented `BrokerOrderManagerError` subclasses for invalid invocation or unrecoverable operational failure. Expected broker outcomes return `BrokerOrderResult(success=False, ...)` with a stable `BOM.*` error code.

---

## 8. Placement pipeline

```text
receive request
 → validate request and client key
 → check idempotency ledger
 → invoke transport
 → on transient failure: reconcile ambiguous placement
 → retry only when reconciliation proves no accepted order
 → normalize raw response
 → fetch/reconcile trades when available
 → atomically publish immutable order snapshot
 → update metrics and optional event
 → return result
```

### 8.1 Placement rules

- **PLACE-BOM-001:** `client_order_id` is unique for the logical request lifetime.
- **PLACE-BOM-002:** Repeating the same client ID and identical canonical request returns the original logical result.
- **PLACE-BOM-003:** Reusing the client ID with different canonical content fails with `BOM.IDEMPOTENCY.CONFLICT`.
- **PLACE-BOM-004:** The request is validated before deduplication lookup can cause a transport operation.
- **PLACE-BOM-005:** A broker rejection becomes a failed result, not an exception that triggers retry.
- **PLACE-BOM-006:** A timeout after request dispatch is ambiguous; reconcile before retry.
- **PLACE-BOM-007:** A successful acknowledgement normalizes to `PENDING`, `OPEN`, `TRIGGER_PENDING`, or `COMPLETE`; it is never assumed `COMPLETE`.
- **PLACE-BOM-008:** The service records broker and client identifiers in every returned snapshot.

### 8.2 Worked placement pseudocode

```python
def place(request: PlaceOrderRequest) -> BrokerOrderResult:
    validate_place_request(request)
    canonical = canonicalize(request)
    existing = ledger.lookup(request.client_order_id)
    if existing is not None:
        return existing.require_same_request(canonical)
    for attempt in range(1, config.max_attempts + 1):
        started = clock.now()
        try:
            raw = transport.place_order(request)
            return seal_place_success(raw, request, attempt, started)
        except AmbiguousTransportError:
            found = transport.find_by_client_order_id(request.client_order_id)
            if found is not None:
                return seal_place_success(found, request, attempt, started)
            if attempt == config.max_attempts:
                return seal_failure("BOM.TRANSPORT.AMBIGUOUS", attempt)
        except RetryableTransportError as error:
            if attempt == config.max_attempts:
                return seal_failure(error.code, attempt)
        except BrokerRejectedError as error:
            return seal_rejection(error, attempt)
        sleep(backoff(attempt))
```

---

## 9. Modification pipeline

Modification is an update request, not replacement order logic. It is allowed only for orders in `PENDING`, `OPEN`, or `TRIGGER_PENDING`. The transport is authoritative for broker-specific amendment restrictions.

| Rule | Requirement |
|---|---|
| MODIFY-BOM-001 | Require non-empty broker order ID. |
| MODIFY-BOM-002 | Require at least one mutable field. |
| MODIFY-BOM-003 | Validate supplied quantity and prices using the same numeric rules as placement. |
| MODIFY-BOM-004 | Do not set a MARKET order price. |
| MODIFY-BOM-005 | Do not remove a required SL or SL-M trigger. |
| MODIFY-BOM-006 | Fetch/reconcile status before retrying an ambiguous modification. |
| MODIFY-BOM-007 | A rejected modification is terminal for that operation and does not alter the last known snapshot. |
| MODIFY-BOM-008 | Return the normalized post-modification snapshot when broker supplies it; otherwise fetch once. |

### 9.1 Modification matrix

| Status | Modify allowed | Outcome |
|---|---:|---|
| PENDING | Yes | submit validated change |
| OPEN | Yes | submit validated change |
| TRIGGER_PENDING | Yes | submit validated change |
| COMPLETE | No | `BOM.STATE.NOT_MODIFIABLE` |
| CANCELLED | No | `BOM.STATE.NOT_MODIFIABLE` |
| REJECTED | No | `BOM.STATE.NOT_MODIFIABLE` |

---

## 10. Cancellation pipeline

Cancellation is idempotent at the façade boundary.

| Rule | Requirement |
|---|---|
| CANCEL-BOM-001 | A cancellation of `CANCELLED` returns a successful no-op snapshot. |
| CANCEL-BOM-002 | A cancellation of `COMPLETE` returns `BOM.STATE.ALREADY_COMPLETE`. |
| CANCEL-BOM-003 | A cancellation of `REJECTED` returns `BOM.STATE.ALREADY_REJECTED`. |
| CANCEL-BOM-004 | Retry a network or temporary server failure only after status reconciliation. |
| CANCEL-BOM-005 | A batch preserves caller order and returns one result per input ID. |
| CANCEL-BOM-006 | Batch processing does not stop on an individual failure. |
| CANCEL-BOM-007 | Duplicate IDs in a batch share the first sealed result. |
| CANCEL-BOM-008 | Batch size over config limit fails before any cancellation is sent. |

### 10.1 Batch cancellation pseudocode

```python
def cancel_many(ids: Sequence[str]) -> tuple[BrokerOrderResult, ...]:
    validate_batch(ids)
    sealed: dict[str, BrokerOrderResult] = {}
    results: list[BrokerOrderResult] = []
    for order_id in ids:
        result = sealed.setdefault(order_id, cancel(order_id))
        results.append(result)
    return tuple(results)
```

---

## 11. Status and execution tracking

### 11.1 Status retrieval

`get_status(id, refresh=True)` obtains the current broker representation, normalizes it, atomically replaces the immutable snapshot when the normalized version is newer, and returns it. `refresh=False` returns the locally sealed snapshot or `BOM.ORDER.NOT_FOUND`.

### 11.2 Execution tracking

`track_executions(id)` fetches the order and trades, normalizes every trade, deduplicates by `(broker_order_id, trade_id)`, sorts executions by `(executed_at, trade_id)`, and seals a new `BrokerOrder`.

| Rule | Requirement |
|---|---|
| EXEC-BOM-001 | Filled quantity is between zero and requested quantity. |
| EXEC-BOM-002 | Remaining quantity equals requested minus filled quantity. |
| EXEC-BOM-003 | Each execution quantity is positive. |
| EXEC-BOM-004 | Average price is absent when filled quantity is zero. |
| EXEC-BOM-005 | Average price is volume weighted where trade prices are present. |
| EXEC-BOM-006 | A duplicate trade ID must not increase fill quantity. |
| EXEC-BOM-007 | A lower reported fill quantity never overwrites a higher locally sealed fill without `BOM.RECONCILIATION.FILL_REGRESSION`. |
| EXEC-BOM-008 | COMPLETE requires remaining quantity zero. |
| EXEC-BOM-009 | Partial fills are valid for OPEN and CANCELLED orders. |
| EXEC-BOM-010 | Execution tracking does not infer a missing trade from order quantities. |

### 11.3 Weighted average calculation

```text
average_price = Σ(execution.quantity × execution.price) / Σ(execution.quantity)
```

Money values use `Decimal`, never `float`. Quantities use positive `int`. A broker-provided average is retained only when its consistency with the trade ledger is within the configured instrument tick; otherwise the service raises `BOM.RECONCILIATION.AVERAGE_PRICE`.

---

## 12. Validation

### 12.1 General rules

| Rule | Requirement |
|---|---|
| VAL-BOM-001 | Request is non-null and uses supported enum values. |
| VAL-BOM-002 | Instrument token is a positive integer. |
| VAL-BOM-003 | Trading symbol is non-empty printable text. |
| VAL-BOM-004 | Quantity is a positive integer. |
| VAL-BOM-005 | Quantity is not boolean, decimal, float, or string. |
| VAL-BOM-006 | Tick size is positive finite `Decimal`. |
| VAL-BOM-007 | Every supplied price is finite, positive `Decimal`. |
| VAL-BOM-008 | Price and trigger are aligned to tick size. |
| VAL-BOM-009 | MARKET has neither price nor trigger. |
| VAL-BOM-010 | LIMIT has price and no trigger. |
| VAL-BOM-011 | SL has both price and trigger. |
| VAL-BOM-012 | SL_M has trigger and no price. |
| VAL-BOM-013 | A BUY SL limit price is not below trigger; a SELL SL limit price is not above trigger. |
| VAL-BOM-014 | Exchange/product combination is supported by platform policy. |
| VAL-BOM-015 | Client order ID contains 1–64 URL-safe characters. |
| VAL-BOM-016 | Naive timestamps are rejected at model boundaries. |
| VAL-BOM-017 | Validity is DAY or IOC only. |
| VAL-BOM-018 | Unknown instrument metadata fails rather than defaulting exchange. |
| VAL-BOM-019 | Modification contains at least one allowed field. |
| VAL-BOM-020 | Cancellation ID is non-empty. |

### 12.2 Validation outcome

Validation failure creates a failed `BrokerOrderResult` where a result type is available, increments `validation_failures`, emits `broker.order.validation_failed` if eventing is configured, and makes zero transport calls. It is never retried.

---

## 13. Retry policy

### 13.1 Retryable classes

| Rule | Failure class | Action |
|---|---|---|
| RETRY-BOM-001 | connection reset | retry boundedly |
| RETRY-BOM-002 | DNS/transient network error | retry boundedly |
| RETRY-BOM-003 | connect/read timeout before dispatch | retry boundedly |
| RETRY-BOM-004 | timeout after possible dispatch | reconcile then retry only if absent |
| RETRY-BOM-005 | HTTP 429 | retry using bounded broker hint when valid |
| RETRY-BOM-006 | HTTP 500/502/503/504 | retry boundedly |
| RETRY-BOM-007 | temporary broker service error | retry boundedly |
| RETRY-BOM-008 | malformed temporary response | fail unless transport classifies retryable |

### 13.2 Never-retry classes

| Rule | Failure class | Action |
|---|---|---|
| RETRY-BOM-101 | local validation failure | fail immediately |
| RETRY-BOM-102 | broker order rejection | fail immediately |
| RETRY-BOM-103 | authentication/authorization failure | fail immediately |
| RETRY-BOM-104 | insufficient funds or margin | fail immediately |
| RETRY-BOM-105 | invalid instrument | fail immediately |
| RETRY-BOM-106 | invalid price/trigger | fail immediately |
| RETRY-BOM-107 | terminal lifecycle state | fail immediately |
| RETRY-BOM-108 | idempotency conflict | fail immediately |

### 13.3 Backoff

```text
delay(attempt) = min(max_delay, initial_delay × multiplier^(attempt - 1))
```

No random jitter is used in core deterministic mode. Deployments requiring distributed jitter apply it in the injected sleeper/policy adapter and record the actual delay for observability.

---

## 14. Kite status mapping

| Kite value | Normalized `BrokerOrderStatus` | Notes |
|---|---|---|
| `PUT ORDER REQ RECEIVED` | PENDING | accepted but not active |
| `VALIDATION PENDING` | PENDING | broker validation pending |
| `OPEN PENDING` | PENDING | transition state |
| `OPEN` | OPEN | active order |
| `TRIGGER PENDING` | TRIGGER_PENDING | stop awaiting trigger |
| `COMPLETE` | COMPLETE | terminal, may include fills |
| `CANCELLED` | CANCELLED | terminal, may retain partial fills |
| `REJECTED` | REJECTED | terminal |
| unknown | error | `BOM.NORMALIZATION.UNKNOWN_STATUS` |

Mapping is case-normalized and whitespace-normalized only. It never maps an unknown broker state to OPEN or COMPLETE.

---

## 15. Health and statistics

### 15.1 Health semantics

`connected` means the most recent transport operation succeeded within the health window and consecutive failures are zero. It does not claim exchange availability, order acceptance, market openness, or authenticated-session validity beyond observed calls.

| Metric | Definition |
|---|---|
| latency | elapsed monotonic time for all transport attempts |
| execution time | elapsed monotonic time from operation start to sealed result |
| failure rate | failed transport attempts / attempts in rolling window |
| consecutive failures | uninterrupted failed transport attempts |
| last success | wall clock at last transport success |
| last failure | wall clock at last transport failure |

### 15.2 Statistics invariants

- **STAT-BOM-001:** Counters are monotonically non-decreasing during manager lifetime.
- **STAT-BOM-002:** A retry increments both retry attempts and relevant operation attempts.
- **STAT-BOM-003:** Validation failure does not increment transport failures.
- **STAT-BOM-004:** Rejection increments rejections but not transport failures.
- **STAT-BOM-005:** Observed executions count unique executions only.
- **STAT-BOM-006:** Snapshots are internally consistent and immutable.

---

## 16. Error catalog

| Code | Meaning | Retry |
|---|---|---|
| `BOM.CONFIG.INVALID` | Invalid manager configuration | No |
| `BOM.VALIDATION.REQUEST` | Generic invalid request | No |
| `BOM.VALIDATION.QUANTITY` | Quantity invalid | No |
| `BOM.VALIDATION.PRICE` | Price invalid | No |
| `BOM.VALIDATION.TRIGGER_PRICE` | Trigger invalid | No |
| `BOM.VALIDATION.TICK_SIZE` | Tick alignment failure | No |
| `BOM.VALIDATION.ORDER_TYPE` | Type/field combination invalid | No |
| `BOM.VALIDATION.PRODUCT` | Product unsupported | No |
| `BOM.VALIDATION.EXCHANGE` | Exchange unsupported | No |
| `BOM.VALIDATION.INSTRUMENT` | Instrument invalid | No |
| `BOM.VALIDATION.CLIENT_ORDER_ID` | Client ID invalid | No |
| `BOM.IDEMPOTENCY.CONFLICT` | Client ID reused differently | No |
| `BOM.ORDER.NOT_FOUND` | No local/broker order | No |
| `BOM.STATE.NOT_MODIFIABLE` | Terminal state cannot change | No |
| `BOM.STATE.ALREADY_COMPLETE` | Cancellation raced completion | No |
| `BOM.STATE.ALREADY_REJECTED` | Cancellation raced rejection | No |
| `BOM.BROKER.REJECTED` | Broker business rejection | No |
| `BOM.BROKER.RATE_LIMITED` | Broker throttled request | Yes |
| `BOM.TRANSPORT.NETWORK` | Network failure | Yes |
| `BOM.TRANSPORT.TIMEOUT` | Unambiguous timeout | Yes |
| `BOM.TRANSPORT.AMBIGUOUS` | Outcome remains unknown | No |
| `BOM.TRANSPORT.TEMPORARY` | Temporary broker failure | Yes |
| `BOM.NORMALIZATION.RESPONSE` | Raw response malformed | No |
| `BOM.NORMALIZATION.UNKNOWN_STATUS` | Status unmapped | No |
| `BOM.RECONCILIATION.FILL_REGRESSION` | Broker fill regressed | No |
| `BOM.RECONCILIATION.AVERAGE_PRICE` | Average mismatch | No |
| `BOM.BATCH.TOO_LARGE` | Batch over configured limit | No |
| `BOM.SERIALIZATION.VERSION` | Unsupported serialized version | No |
| `BOM.INTERNAL.INVARIANT` | Internal invariant failed | No |

---

## 17. Security

- **SEC-BOM-001:** No credential is accepted as an order request field.
- **SEC-BOM-002:** Do not log access tokens, authorization headers, or full raw broker payloads containing sensitive data.
- **SEC-BOM-003:** Redact account identifiers in exception text unless an approved audit sink requires them.
- **SEC-BOM-004:** Bound all strings copied from broker payloads.
- **SEC-BOM-005:** Treat raw broker payloads as untrusted input.
- **SEC-BOM-006:** Validate client IDs before using them in lookup keys.
- **SEC-BOM-007:** Serialize only explicit public fields.
- **SEC-BOM-008:** Event payloads must exclude credentials and raw authorization metadata.

---

## 18. Thread safety and determinism

The manager uses one private re-entrant lock for atomic ledger publication and statistics updates. It releases the lock before transport I/O. A second in-flight map keyed by client order ID ensures concurrent equivalent placements coalesce onto one logical operation.

```text
Thread A: validate → reserve client ID → unlock → transport
Thread B: validate → sees reservation → waits for sealed result
Thread A: normalize → lock → publish immutable snapshot → notify → unlock
Thread B: returns same sealed result
```

| Rule | Requirement |
|---|---|
| CONC-BOM-001 | Never hold a state lock during network I/O. |
| CONC-BOM-002 | Same client ID has one in-flight placement. |
| CONC-BOM-003 | Different IDs may execute concurrently. |
| CONC-BOM-004 | Snapshot replacement is atomic. |
| CONC-BOM-005 | Public models never expose mutable lists or mappings. |
| DET-BOM-001 | Inject clock and sleeper. |
| DET-BOM-002 | Inject identifier generator. |
| DET-BOM-003 | Sort executions deterministically. |
| DET-BOM-004 | Preserve caller batch order. |

---

## 19. Serialization

Every model supports `to_dict()`, `to_json()`, `from_dict()`, and `from_json()` where reconstruction is meaningful. The envelope is explicit:

```json
{
  "schema": "theta.broker_order",
  "version": 1,
  "payload": {}
}
```

| Rule | Requirement |
|---|---|
| SER-BOM-001 | Datetimes serialize as RFC 3339 UTC offsets. |
| SER-BOM-002 | Decimal values serialize as canonical strings. |
| SER-BOM-003 | Enums serialize as stable value strings. |
| SER-BOM-004 | Tuples serialize as arrays and deserialize to tuples. |
| SER-BOM-005 | Unknown envelope version fails closed. |
| SER-BOM-006 | Missing required fields fail closed. |
| SER-BOM-007 | Extra fields are ignored only in forward-compatible read mode. |
| SER-BOM-008 | Serialization is stable for equal models. |

---

## 20. Lifecycle state machine

```text
                place acknowledged
NEW ───────────────────────────► PENDING ─────► OPEN ─────► COMPLETE
                                   │              │  │
                                   │              │  └────────► CANCELLED
                                   │              └───────────► REJECTED
                                   └────────────► TRIGGER_PENDING
                                                    │       │
                                                    └──────► OPEN / CANCELLED
```

| Rule | Requirement |
|---|---|
| STATE-BOM-001 | COMPLETE, CANCELLED, and REJECTED are terminal. |
| STATE-BOM-002 | COMPLETE may not transition to another status. |
| STATE-BOM-003 | CANCELLED may retain executions. |
| STATE-BOM-004 | REJECTED has zero filled quantity unless broker supplies a contradictory audited record, which fails reconciliation. |
| STATE-BOM-005 | Status transitions are validated before snapshot publication. |

---

## 21. Event bus topics

Event publication is optional and observational. Failed publication never changes broker-operation outcome.

| Topic | Payload |
|---|---|
| `broker.order.placed` | `BrokerOrderResult` |
| `broker.order.modified` | `BrokerOrderResult` |
| `broker.order.cancelled` | `BrokerOrderResult` |
| `broker.order.status_updated` | `BrokerOrder` |
| `broker.order.execution_observed` | `BrokerExecution` |
| `broker.order.rejected` | `BrokerOrderResult` |
| `broker.order.validation_failed` | stable failure result |
| `broker.order.transport_failed` | stable failure result |
| `broker.health.updated` | `BrokerHealth` |

---

## 22. Consumer integration

### 22.1 System Orchestrator

The orchestrator owns authorisation and sequencing. It may invoke this service after its own policy gates have approved an operation. It aggregates health and passes read-only outputs to downstream consumers.

### 22.2 Live Trading Runner

The live runner constructs an injected live adapter around `BaseBrokerClient` or `KiteBrokerClient`, constructs the service from projected config, and invokes the façade. It never bypasses validation/retry by calling Kite directly for supported order operations.

### 22.3 Paper Trading Runner

The paper runner injects `PaperBrokerOrderTransport`. It receives the identical public models and method signatures. Simulated fills are transport behavior, not façade branching.

### 22.4 `execution/order_manager.py`

The order manager remains broker-neutral and communicates only through `BaseBrokerClient`. A production BaseBrokerClient adapter may delegate to `BrokerOrderManager`; this does not make the plan manager dependent on Kite or on a direct import of this module.

### 22.5 APME

APME receives order status and execution artifacts from the System Orchestrator as read-only facts. It never calls `place`, `modify`, or `cancel` directly.

---

## 23. Testing requirements

The implementation must provide `tests/test_broker_order_manager.py` and achieve at least 95% line coverage for the production module.

| Test ID | Scenario |
|---|---|
| TEST-BOM-001 | Valid MARKET placement |
| TEST-BOM-002 | Valid LIMIT placement |
| TEST-BOM-003 | Valid SL placement |
| TEST-BOM-004 | Valid SL-M placement |
| TEST-BOM-005 | Invalid quantity prevents transport call |
| TEST-BOM-006 | Invalid tick alignment prevents transport call |
| TEST-BOM-007 | Invalid product/exchange prevents transport call |
| TEST-BOM-008 | Rejection is not retried |
| TEST-BOM-009 | Network failure retries boundedly |
| TEST-BOM-010 | Timeout reconciles by client ID |
| TEST-BOM-011 | Ambiguous unresolved result is not replayed |
| TEST-BOM-012 | Same ID same request deduplicates |
| TEST-BOM-013 | Same ID different request conflicts |
| TEST-BOM-014 | Partial fills calculate average |
| TEST-BOM-015 | Duplicate trade ID is ignored |
| TEST-BOM-016 | Fill regression fails closed |
| TEST-BOM-017 | Terminal order cannot modify |
| TEST-BOM-018 | Batch cancellation preserves order |
| TEST-BOM-019 | Batch duplicate IDs coalesce |
| TEST-BOM-020 | Serialization round trip |
| TEST-BOM-021 | Unsupported version rejects |
| TEST-BOM-022 | Health rolling calculations |
| TEST-BOM-023 | Concurrent same-key placement makes one transport call |
| TEST-BOM-024 | Concurrent readers see sealed snapshots |
| TEST-BOM-025 | Paper transport conforms |

### 23.1 Fake transport

```python
class FakeBrokerOrderTransport:
    """Deterministic fake transport for unit tests."""

    def __init__(self, scripted: Sequence[object]) -> None:
        self.scripted = deque(scripted)
        self.calls: list[tuple[str, object]] = []

    def place_order(self, request: PlaceOrderRequest) -> Mapping[str, object]:
        self.calls.append(("place", request))
        outcome = self.scripted.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return cast(Mapping[str, object], outcome)
```

Tests use a fixed UTC clock, no-op sleeper, deterministic client IDs, and raw fixture mappings. No unit test uses a live Kite account.

---

## 24. Implementation checklist

- [ ] Define enum aliases aligned with `base_broker`.
- [ ] Define frozen public models with Google-style docstrings.
- [ ] Define transport protocol and adapter contract.
- [ ] Validate configuration at construction.
- [ ] Validate placement, modification, and cancellation inputs.
- [ ] Implement idempotency ledger and in-flight coalescing.
- [ ] Implement retry classifier and bounded deterministic backoff.
- [ ] Implement ambiguous placement reconciliation.
- [ ] Normalize Kite order and trade payloads.
- [ ] Implement execution ledger and weighted average.
- [ ] Implement lifecycle transition validation.
- [ ] Implement health and statistics snapshots.
- [ ] Implement versioned serializers.
- [ ] Implement optional event publisher isolation.
- [ ] Implement live and paper adapter conformance tests.
- [ ] Add unit tests and coverage gate.

---

## 25. Definition of Done

The module is done only when all of the following are true:

1. It implements the public models named in this specification.
2. It accepts injected live and paper transports.
3. It never imports Kite as its only path or loads `.env`.
4. It validates before transport.
5. It retries only transient errors.
6. It reconciles ambiguous placement before replay.
7. It tracks partial fills and trades correctly.
8. It produces immutable, versioned outputs.
9. It is thread-safe and deterministic under injected dependencies.
10. It meets the specified test coverage threshold.
11. It does not evaluate strategies, calculate indicators or risk, manage portfolios, or decide trades.

---

## 26. Non-goals

This module is not a strategy engine, signal engine, risk engine, portfolio ledger, market-data service, authentication client, order-plan manager, OMS replacement, exchange gateway, or backtesting simulator. It intentionally has no opinion on whether a trade should occur.

---

# Appendices

## Appendix A — Worked MARKET order

```json
{
  "client_order_id": "live-20260805-000001",
  "instrument_token": 256265,
  "trading_symbol": "NIFTY 50",
  "exchange": "NSE",
  "side": "BUY",
  "order_type": "MARKET",
  "product": "MIS",
  "validity": "DAY",
  "quantity": 50,
  "tick_size": "0.05"
}
```

Expected result: request validates, transport is called once, returned broker ID is stored, and status remains broker-reported rather than assumed filled.

## Appendix B — Worked LIMIT order

```text
BUY NFO option, quantity 50, LIMIT 112.50, DAY
```

The `price` must be a positive `Decimal` aligned to the instrument's `0.05` tick. A response reporting OPEN and zero fills produces `remaining_quantity=50` and `average_price=None`.

## Appendix C — Worked SL and SL-M orders

| Type | Side | Price | Trigger | Valid |
|---|---|---:|---:|---:|
| SL | BUY | 104.00 | 103.50 | Yes |
| SL | SELL | 96.00 | 96.50 | Yes |
| SL-M | BUY | absent | 103.50 | Yes |
| SL-M | SELL | absent | 96.50 | Yes |

An SL request with price `103.00` and BUY trigger `103.50` fails `BOM.VALIDATION.TRIGGER_PRICE`.

## Appendix D — Raw Kite order response map

| Kite field | Canonical field | Handling |
|---|---|---|
| `order_id` | `broker_order_id` | required non-empty string |
| `status` | `status` | map through status table |
| `quantity` | `quantity` | positive integer |
| `filled_quantity` | `filled_quantity` | non-negative integer |
| `pending_quantity` | `remaining_quantity` | must reconcile |
| `average_price` | `average_price` | Decimal or absent |
| `price` | `price` | Decimal or absent |
| `trigger_price` | `trigger_price` | Decimal or absent |
| `order_timestamp` | `created_at` | timezone-aware |
| `exchange_timestamp` | `updated_at` | timezone-aware |
| `status_message` | `status_message` | bounded text |

## Appendix E — Raw Kite trade response map

| Kite field | Canonical field |
|---|---|
| `trade_id` | `trade_id` |
| `order_id` | `broker_order_id` |
| `quantity` | `quantity` |
| `average_price` | `price` |
| `fill_timestamp` | `executed_at` |
| `exchange` | `exchange` |

## Appendix F — Placement failure matrix

| Situation | Transport calls | Result |
|---|---:|---|
| Invalid quantity | 0 | validation failure |
| Broker rejection | 1 | rejected, no retry |
| Network before dispatch | ≤ max attempts | retryable failure or success |
| Timeout after dispatch, found by tag | 1 lookup | reconciled success |
| Timeout after dispatch, absent | ≤ max attempts | retry or ambiguous failure |
| Rate limit | ≤ max attempts | bounded retry |
| Unknown response status | 1 | normalization failure |

## Appendix G — Modification examples

```text
OPEN LIMIT BUY 50 @ 100.00
modify price to 99.50
→ validate tick alignment
→ submit
→ normalize post-change OPEN snapshot
```

```text
COMPLETE order
modify price to 99.50
→ BOM.STATE.NOT_MODIFIABLE
→ no transport modification call
```

## Appendix H — Cancellation examples

| Existing status | `cancel()` result |
|---|---|
| OPEN | submit cancellation |
| TRIGGER_PENDING | submit cancellation |
| CANCELLED | successful idempotent no-op |
| COMPLETE | `BOM.STATE.ALREADY_COMPLETE` |
| REJECTED | `BOM.STATE.ALREADY_REJECTED` |

## Appendix I — Idempotency contract

The idempotency key is `client_order_id`. Its canonical request fingerprint includes instrument token, exchange, side, type, product, validity, quantity, price, trigger price, and variety. Metadata that does not affect broker behavior is excluded.

| First call | Second call | Outcome |
|---|---|---|
| same ID, same content, complete | same | original result |
| same ID, same content, in flight | same | waits and returns sealed result |
| same ID, different quantity | different | conflict |
| new ID, same content | distinct | independent operation |

## Appendix J — Reconciliation algorithm

```text
ambiguous placement
 → query by client order ID
 → found: normalize and seal
 → not found and attempts remain: retry
 → not found and no attempts: ambiguous failure
 → lookup failure: ambiguous failure; never blind replay
```

## Appendix K — Concurrency sketches

```text
T1 reserve key K ──── transport ──── seal snapshot S1
T2 observe K ──────── wait ───────── return S1
T3 status read S0 ─── atomic swap ── read S1
```

No public method returns a mutable reference to ledger internals.

## Appendix L — Serialization examples

```json
{
  "schema": "theta.broker_execution",
  "version": 1,
  "payload": {
    "trade_id": "123",
    "broker_order_id": "456",
    "quantity": 25,
    "price": "102.50",
    "executed_at": "2026-08-05T09:15:01+05:30",
    "exchange": "NFO"
  }
}
```

## Appendix M — Paper adapter notes

The paper adapter owns simulation rules: acceptance, matching, latency simulation, and fake trade generation. It must preserve client ID lookup, raw-field contract, status mapping capability, and deterministic scripted behavior. The façade must not branch on `is_paper`.

## Appendix N — Live adapter notes

The live adapter wraps the existing authenticated `BaseBrokerClient` or `KiteBrokerClient`. It translates shared request models to Kite wire fields and raw Kite responses to mapping payloads. It translates broker exceptions into typed transport exceptions; retry policy stays in this module.

## Appendix O — Error-to-outcome examples

| Input / event | Code | Caller action |
|---|---|---|
| quantity 0 | `BOM.VALIDATION.QUANTITY` | correct request |
| rejected due to freeze | `BOM.BROKER.REJECTED` | inspect broker reason |
| HTTP 503 exhausted | `BOM.TRANSPORT.TEMPORARY` | reconcile/alert |
| same key changed price | `BOM.IDEMPOTENCY.CONFLICT` | create deliberate new operation |
| unknown raw status | `BOM.NORMALIZATION.UNKNOWN_STATUS` | quarantine and investigate |

## Appendix P — Benchmark requirements

| Benchmark | Target |
|---|---|
| Local validation p95 | under 1 ms |
| Snapshot lookup p95 | under 1 ms |
| Serialization of one order p95 | under 2 ms |
| 1,000 concurrent read snapshots | no torn state |
| Fake transport placement overhead p95 | under 2 ms excluding simulated I/O |

Benchmark measurements use monotonic time, fixed hardware metadata, warmed interpreter, and no real network.

## Appendix Q — Observability fields

Every structured log/event includes: operation, client order ID where permitted, broker order ID where known, attempt, error code, retryable flag, elapsed milliseconds, normalized status, and correlation ID. It excludes tokens and authorization headers.

## Appendix R — Migration notes

1. Keep existing callers on `BaseBrokerClient`.
2. Build a production adapter delegating primitive order methods to the manager.
3. Introduce client order IDs at the orchestration boundary.
4. Shadow-normalize returned orders during migration.
5. Enable retry/reconciliation after comparison metrics are stable.
6. Route paper runner through paper transport.
7. Remove direct supported-order calls to Kite from runners.

No migration step changes Execution Engine ownership or lets APME submit orders.

## Appendix S — Fault injection catalogue

| Fault | Expected invariant |
|---|---|
| socket reset | retry budget respected |
| timeout after request | reconciliation before replay |
| duplicate trade | fill count unchanged |
| stale lower fill | fail closed |
| malformed order ID | normalization fails |
| event publisher crash | operation result unchanged |
| clock jump wall time | latency still monotonic |
| batch duplicate ID | same sealed result |

## Appendix T — Glossary

| Term | Meaning |
|---|---|
| acknowledgement | broker accepted request for processing |
| execution | a broker-reported trade/fill |
| fill | quantity executed against an order |
| idempotency key | caller key identifying one logical placement |
| logical operation | one caller-intended operation across retries |
| reconciliation | resolving uncertain outcome from broker evidence |
| transport | injected I/O adapter |
| terminal state | status disallowing further modification |

## Appendix U — Validation decision table

| Type | Price present | Trigger present | Decision |
|---|---:|---:|---|
| MARKET | No | No | accept |
| MARKET | Yes | No | reject |
| LIMIT | Yes | No | accept |
| LIMIT | No | No | reject |
| SL | Yes | Yes | accept |
| SL | Yes | No | reject |
| SL-M | No | Yes | accept |
| SL-M | Yes | Yes | reject |

## Appendix V — Statistics examples

After one successful placement that timed out once before successful retry:

```text
placement_attempts = 2
placement_successes = 1
retry_attempts = 1
transport_failures = 1
rejections = 0
```

After a locally invalid placement:

```text
placement_attempts = unchanged
validation_failures = previous + 1
transport_failures = unchanged
```

## Appendix W — Lifecycle transition table

| From | To | Allowed |
|---|---|---:|
| PENDING | OPEN | Yes |
| PENDING | TRIGGER_PENDING | Yes |
| PENDING | REJECTED | Yes |
| OPEN | COMPLETE | Yes |
| OPEN | CANCELLED | Yes |
| TRIGGER_PENDING | OPEN | Yes |
| TRIGGER_PENDING | CANCELLED | Yes |
| COMPLETE | OPEN | No |
| CANCELLED | COMPLETE | No |
| REJECTED | OPEN | No |

## Appendix X — Compliance assertions

- This module has no import of strategy or risk packages.
- This module has no indicator formula.
- This module has no portfolio mutation API.
- This module has no trade-decision API.
- This module has no `.env` loading.
- This module has no direct mandatory `kiteconnect` dependency.
- The only transport dependency is the injected protocol.

## Appendix Y — Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-05 | Initial locked v1.0 broker-order-manager specification. |

## Appendix Z — Final acceptance matrix

| Area | Acceptance evidence |
|---|---|
| Boundary | imports and tests show no trade decision logic |
| Validation | invalid requests make zero transport calls |
| Retry | transient-only retry tests pass |
| Idempotency | concurrent same-key test passes |
| Tracking | partial fill and duplicate-trade tests pass |
| Health | rolling metric tests pass |
| Serialization | versioned round-trip tests pass |
| Integration | live and paper adapters conform |
| Quality | ≥95% coverage achieved |
| Freeze | pipeline ownership remains unchanged |

---

# Extended Operational Appendices

## Appendix AA — Operation contracts

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AA-01 | All public operations validate before transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-01 | The returned result has a stable operation name. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-01 | Broker-native payloads remain transport-private. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-01 | A sealed order snapshot is immutable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-01 | Metrics update atomically with result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-02 | All public operations validate before transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-02 | The returned result has a stable operation name. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-02 | Broker-native payloads remain transport-private. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-02 | A sealed order snapshot is immutable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-02 | Metrics update atomically with result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-03 | All public operations validate before transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-03 | The returned result has a stable operation name. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-03 | Broker-native payloads remain transport-private. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-03 | A sealed order snapshot is immutable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-03 | Metrics update atomically with result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-04 | All public operations validate before transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-04 | The returned result has a stable operation name. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-04 | Broker-native payloads remain transport-private. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-04 | A sealed order snapshot is immutable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-04 | Metrics update atomically with result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-05 | All public operations validate before transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-05 | The returned result has a stable operation name. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-05 | Broker-native payloads remain transport-private. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-05 | A sealed order snapshot is immutable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-05 | Metrics update atomically with result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-06 | All public operations validate before transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-06 | The returned result has a stable operation name. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-06 | Broker-native payloads remain transport-private. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-06 | A sealed order snapshot is immutable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-06 | Metrics update atomically with result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-07 | All public operations validate before transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-07 | The returned result has a stable operation name. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-07 | Broker-native payloads remain transport-private. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-07 | A sealed order snapshot is immutable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-07 | Metrics update atomically with result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-08 | All public operations validate before transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-08 | The returned result has a stable operation name. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-08 | Broker-native payloads remain transport-private. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-08 | A sealed order snapshot is immutable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AA-08 | Metrics update atomically with result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AA-VERIFY-01:** Verify that all public operations validate before transport.
- **AA-VERIFY-02:** Verify that the returned result has a stable operation name.
- **AA-VERIFY-03:** Verify that broker-native payloads remain transport-private.
- **AA-VERIFY-04:** Verify that a sealed order snapshot is immutable.
- **AA-VERIFY-05:** Verify that metrics update atomically with result sealing.
- **AA-VERIFY-06:** Verify that all public operations validate before transport.
- **AA-VERIFY-07:** Verify that the returned result has a stable operation name.
- **AA-VERIFY-08:** Verify that broker-native payloads remain transport-private.
- **AA-VERIFY-09:** Verify that a sealed order snapshot is immutable.
- **AA-VERIFY-10:** Verify that metrics update atomically with result sealing.
- **AA-VERIFY-11:** Verify that all public operations validate before transport.
- **AA-VERIFY-12:** Verify that the returned result has a stable operation name.

### Operator interpretation
1. For this scenario, All public operations validate before transport. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, The returned result has a stable operation name. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, Broker-native payloads remain transport-private. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, A sealed order snapshot is immutable. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Metrics update atomically with result sealing. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, All public operations validate before transport. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, The returned result has a stable operation name. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, Broker-native payloads remain transport-private. The caller records the returned result and does not infer a trading decision from it.

## Appendix AB — Placement scenarios

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AB-01 | A MARKET acknowledgement may remain PENDING. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-01 | A LIMIT order may be OPEN with zero fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-01 | An SL order must preserve its trigger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-01 | An SL-M order must have no limit price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-01 | A broker rejection ends the logical operation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-02 | A MARKET acknowledgement may remain PENDING. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-02 | A LIMIT order may be OPEN with zero fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-02 | An SL order must preserve its trigger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-02 | An SL-M order must have no limit price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-02 | A broker rejection ends the logical operation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-03 | A MARKET acknowledgement may remain PENDING. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-03 | A LIMIT order may be OPEN with zero fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-03 | An SL order must preserve its trigger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-03 | An SL-M order must have no limit price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-03 | A broker rejection ends the logical operation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-04 | A MARKET acknowledgement may remain PENDING. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-04 | A LIMIT order may be OPEN with zero fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-04 | An SL order must preserve its trigger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-04 | An SL-M order must have no limit price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-04 | A broker rejection ends the logical operation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-05 | A MARKET acknowledgement may remain PENDING. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-05 | A LIMIT order may be OPEN with zero fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-05 | An SL order must preserve its trigger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-05 | An SL-M order must have no limit price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-05 | A broker rejection ends the logical operation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-06 | A MARKET acknowledgement may remain PENDING. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-06 | A LIMIT order may be OPEN with zero fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-06 | An SL order must preserve its trigger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-06 | An SL-M order must have no limit price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-06 | A broker rejection ends the logical operation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-07 | A MARKET acknowledgement may remain PENDING. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-07 | A LIMIT order may be OPEN with zero fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-07 | An SL order must preserve its trigger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-07 | An SL-M order must have no limit price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-07 | A broker rejection ends the logical operation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-08 | A MARKET acknowledgement may remain PENDING. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-08 | A LIMIT order may be OPEN with zero fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-08 | An SL order must preserve its trigger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-08 | An SL-M order must have no limit price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AB-08 | A broker rejection ends the logical operation. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AB-VERIFY-01:** Verify that a market acknowledgement may remain pending.
- **AB-VERIFY-02:** Verify that a limit order may be open with zero fills.
- **AB-VERIFY-03:** Verify that an sl order must preserve its trigger.
- **AB-VERIFY-04:** Verify that an sl-m order must have no limit price.
- **AB-VERIFY-05:** Verify that a broker rejection ends the logical operation.
- **AB-VERIFY-06:** Verify that a market acknowledgement may remain pending.
- **AB-VERIFY-07:** Verify that a limit order may be open with zero fills.
- **AB-VERIFY-08:** Verify that an sl order must preserve its trigger.
- **AB-VERIFY-09:** Verify that an sl-m order must have no limit price.
- **AB-VERIFY-10:** Verify that a broker rejection ends the logical operation.
- **AB-VERIFY-11:** Verify that a market acknowledgement may remain pending.
- **AB-VERIFY-12:** Verify that a limit order may be open with zero fills.

### Operator interpretation
1. For this scenario, A MARKET acknowledgement may remain PENDING. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, A LIMIT order may be OPEN with zero fills. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, An SL order must preserve its trigger. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, An SL-M order must have no limit price. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, A broker rejection ends the logical operation. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, A MARKET acknowledgement may remain PENDING. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, A LIMIT order may be OPEN with zero fills. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, An SL order must preserve its trigger. The caller records the returned result and does not infer a trading decision from it.

## Appendix AC — Modification scenarios

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AC-01 | Only mutable fields are sent to the transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-01 | No-op modifications fail validation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-01 | A terminal order is never modified. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-01 | Ambiguous modification is reconciled by status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-01 | Post-modification state is fetched if omitted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-02 | Only mutable fields are sent to the transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-02 | No-op modifications fail validation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-02 | A terminal order is never modified. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-02 | Ambiguous modification is reconciled by status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-02 | Post-modification state is fetched if omitted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-03 | Only mutable fields are sent to the transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-03 | No-op modifications fail validation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-03 | A terminal order is never modified. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-03 | Ambiguous modification is reconciled by status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-03 | Post-modification state is fetched if omitted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-04 | Only mutable fields are sent to the transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-04 | No-op modifications fail validation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-04 | A terminal order is never modified. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-04 | Ambiguous modification is reconciled by status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-04 | Post-modification state is fetched if omitted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-05 | Only mutable fields are sent to the transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-05 | No-op modifications fail validation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-05 | A terminal order is never modified. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-05 | Ambiguous modification is reconciled by status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-05 | Post-modification state is fetched if omitted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-06 | Only mutable fields are sent to the transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-06 | No-op modifications fail validation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-06 | A terminal order is never modified. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-06 | Ambiguous modification is reconciled by status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-06 | Post-modification state is fetched if omitted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-07 | Only mutable fields are sent to the transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-07 | No-op modifications fail validation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-07 | A terminal order is never modified. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-07 | Ambiguous modification is reconciled by status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-07 | Post-modification state is fetched if omitted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-08 | Only mutable fields are sent to the transport. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-08 | No-op modifications fail validation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-08 | A terminal order is never modified. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-08 | Ambiguous modification is reconciled by status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AC-08 | Post-modification state is fetched if omitted. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AC-VERIFY-01:** Verify that only mutable fields are sent to the transport.
- **AC-VERIFY-02:** Verify that no-op modifications fail validation.
- **AC-VERIFY-03:** Verify that a terminal order is never modified.
- **AC-VERIFY-04:** Verify that ambiguous modification is reconciled by status.
- **AC-VERIFY-05:** Verify that post-modification state is fetched if omitted.
- **AC-VERIFY-06:** Verify that only mutable fields are sent to the transport.
- **AC-VERIFY-07:** Verify that no-op modifications fail validation.
- **AC-VERIFY-08:** Verify that a terminal order is never modified.
- **AC-VERIFY-09:** Verify that ambiguous modification is reconciled by status.
- **AC-VERIFY-10:** Verify that post-modification state is fetched if omitted.
- **AC-VERIFY-11:** Verify that only mutable fields are sent to the transport.
- **AC-VERIFY-12:** Verify that no-op modifications fail validation.

### Operator interpretation
1. For this scenario, Only mutable fields are sent to the transport. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, No-op modifications fail validation. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, A terminal order is never modified. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Ambiguous modification is reconciled by status. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Post-modification state is fetched if omitted. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Only mutable fields are sent to the transport. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, No-op modifications fail validation. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, A terminal order is never modified. The caller records the returned result and does not infer a trading decision from it.

## Appendix AD — Cancellation scenarios

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AD-01 | Cancellation is requested only for active states. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-01 | Cancellation may race with a final fill. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-01 | A complete order is not falsely marked cancelled. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-01 | A cancelled partial order retains fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-01 | One batch item cannot abort another item. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-02 | Cancellation is requested only for active states. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-02 | Cancellation may race with a final fill. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-02 | A complete order is not falsely marked cancelled. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-02 | A cancelled partial order retains fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-02 | One batch item cannot abort another item. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-03 | Cancellation is requested only for active states. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-03 | Cancellation may race with a final fill. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-03 | A complete order is not falsely marked cancelled. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-03 | A cancelled partial order retains fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-03 | One batch item cannot abort another item. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-04 | Cancellation is requested only for active states. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-04 | Cancellation may race with a final fill. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-04 | A complete order is not falsely marked cancelled. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-04 | A cancelled partial order retains fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-04 | One batch item cannot abort another item. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-05 | Cancellation is requested only for active states. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-05 | Cancellation may race with a final fill. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-05 | A complete order is not falsely marked cancelled. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-05 | A cancelled partial order retains fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-05 | One batch item cannot abort another item. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-06 | Cancellation is requested only for active states. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-06 | Cancellation may race with a final fill. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-06 | A complete order is not falsely marked cancelled. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-06 | A cancelled partial order retains fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-06 | One batch item cannot abort another item. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-07 | Cancellation is requested only for active states. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-07 | Cancellation may race with a final fill. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-07 | A complete order is not falsely marked cancelled. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-07 | A cancelled partial order retains fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-07 | One batch item cannot abort another item. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-08 | Cancellation is requested only for active states. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-08 | Cancellation may race with a final fill. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-08 | A complete order is not falsely marked cancelled. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-08 | A cancelled partial order retains fills. | Record deterministic `BOM` outcome and immutable snapshot. |
| AD-08 | One batch item cannot abort another item. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AD-VERIFY-01:** Verify that cancellation is requested only for active states.
- **AD-VERIFY-02:** Verify that cancellation may race with a final fill.
- **AD-VERIFY-03:** Verify that a complete order is not falsely marked cancelled.
- **AD-VERIFY-04:** Verify that a cancelled partial order retains fills.
- **AD-VERIFY-05:** Verify that one batch item cannot abort another item.
- **AD-VERIFY-06:** Verify that cancellation is requested only for active states.
- **AD-VERIFY-07:** Verify that cancellation may race with a final fill.
- **AD-VERIFY-08:** Verify that a complete order is not falsely marked cancelled.
- **AD-VERIFY-09:** Verify that a cancelled partial order retains fills.
- **AD-VERIFY-10:** Verify that one batch item cannot abort another item.
- **AD-VERIFY-11:** Verify that cancellation is requested only for active states.
- **AD-VERIFY-12:** Verify that cancellation may race with a final fill.

### Operator interpretation
1. For this scenario, Cancellation is requested only for active states. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Cancellation may race with a final fill. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, A complete order is not falsely marked cancelled. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, A cancelled partial order retains fills. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, One batch item cannot abort another item. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Cancellation is requested only for active states. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Cancellation may race with a final fill. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, A complete order is not falsely marked cancelled. The caller records the returned result and does not infer a trading decision from it.

## Appendix AE — Status reconciliation

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AE-01 | The broker is authoritative for current raw status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-01 | Unknown status is quarantined, not guessed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-01 | Status timestamps must be timezone-aware. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-01 | Older snapshots cannot overwrite newer snapshots. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-01 | A stale response is retained only for audit diagnostics. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-02 | The broker is authoritative for current raw status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-02 | Unknown status is quarantined, not guessed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-02 | Status timestamps must be timezone-aware. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-02 | Older snapshots cannot overwrite newer snapshots. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-02 | A stale response is retained only for audit diagnostics. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-03 | The broker is authoritative for current raw status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-03 | Unknown status is quarantined, not guessed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-03 | Status timestamps must be timezone-aware. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-03 | Older snapshots cannot overwrite newer snapshots. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-03 | A stale response is retained only for audit diagnostics. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-04 | The broker is authoritative for current raw status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-04 | Unknown status is quarantined, not guessed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-04 | Status timestamps must be timezone-aware. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-04 | Older snapshots cannot overwrite newer snapshots. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-04 | A stale response is retained only for audit diagnostics. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-05 | The broker is authoritative for current raw status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-05 | Unknown status is quarantined, not guessed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-05 | Status timestamps must be timezone-aware. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-05 | Older snapshots cannot overwrite newer snapshots. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-05 | A stale response is retained only for audit diagnostics. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-06 | The broker is authoritative for current raw status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-06 | Unknown status is quarantined, not guessed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-06 | Status timestamps must be timezone-aware. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-06 | Older snapshots cannot overwrite newer snapshots. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-06 | A stale response is retained only for audit diagnostics. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-07 | The broker is authoritative for current raw status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-07 | Unknown status is quarantined, not guessed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-07 | Status timestamps must be timezone-aware. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-07 | Older snapshots cannot overwrite newer snapshots. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-07 | A stale response is retained only for audit diagnostics. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-08 | The broker is authoritative for current raw status. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-08 | Unknown status is quarantined, not guessed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-08 | Status timestamps must be timezone-aware. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-08 | Older snapshots cannot overwrite newer snapshots. | Record deterministic `BOM` outcome and immutable snapshot. |
| AE-08 | A stale response is retained only for audit diagnostics. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AE-VERIFY-01:** Verify that the broker is authoritative for current raw status.
- **AE-VERIFY-02:** Verify that unknown status is quarantined, not guessed.
- **AE-VERIFY-03:** Verify that status timestamps must be timezone-aware.
- **AE-VERIFY-04:** Verify that older snapshots cannot overwrite newer snapshots.
- **AE-VERIFY-05:** Verify that a stale response is retained only for audit diagnostics.
- **AE-VERIFY-06:** Verify that the broker is authoritative for current raw status.
- **AE-VERIFY-07:** Verify that unknown status is quarantined, not guessed.
- **AE-VERIFY-08:** Verify that status timestamps must be timezone-aware.
- **AE-VERIFY-09:** Verify that older snapshots cannot overwrite newer snapshots.
- **AE-VERIFY-10:** Verify that a stale response is retained only for audit diagnostics.
- **AE-VERIFY-11:** Verify that the broker is authoritative for current raw status.
- **AE-VERIFY-12:** Verify that unknown status is quarantined, not guessed.

### Operator interpretation
1. For this scenario, The broker is authoritative for current raw status. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Unknown status is quarantined, not guessed. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, Status timestamps must be timezone-aware. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Older snapshots cannot overwrite newer snapshots. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, A stale response is retained only for audit diagnostics. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, The broker is authoritative for current raw status. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Unknown status is quarantined, not guessed. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, Status timestamps must be timezone-aware. The caller records the returned result and does not infer a trading decision from it.

## Appendix AF — Execution reconciliation

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AF-01 | Trades are keyed by order and trade ID. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-01 | Execution quantities must be positive integers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-01 | Trade timestamps order the immutable ledger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-01 | Duplicate trades never double count. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-01 | Fill regression fails closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-02 | Trades are keyed by order and trade ID. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-02 | Execution quantities must be positive integers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-02 | Trade timestamps order the immutable ledger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-02 | Duplicate trades never double count. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-02 | Fill regression fails closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-03 | Trades are keyed by order and trade ID. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-03 | Execution quantities must be positive integers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-03 | Trade timestamps order the immutable ledger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-03 | Duplicate trades never double count. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-03 | Fill regression fails closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-04 | Trades are keyed by order and trade ID. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-04 | Execution quantities must be positive integers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-04 | Trade timestamps order the immutable ledger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-04 | Duplicate trades never double count. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-04 | Fill regression fails closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-05 | Trades are keyed by order and trade ID. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-05 | Execution quantities must be positive integers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-05 | Trade timestamps order the immutable ledger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-05 | Duplicate trades never double count. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-05 | Fill regression fails closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-06 | Trades are keyed by order and trade ID. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-06 | Execution quantities must be positive integers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-06 | Trade timestamps order the immutable ledger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-06 | Duplicate trades never double count. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-06 | Fill regression fails closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-07 | Trades are keyed by order and trade ID. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-07 | Execution quantities must be positive integers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-07 | Trade timestamps order the immutable ledger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-07 | Duplicate trades never double count. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-07 | Fill regression fails closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-08 | Trades are keyed by order and trade ID. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-08 | Execution quantities must be positive integers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-08 | Trade timestamps order the immutable ledger. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-08 | Duplicate trades never double count. | Record deterministic `BOM` outcome and immutable snapshot. |
| AF-08 | Fill regression fails closed. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AF-VERIFY-01:** Verify that trades are keyed by order and trade id.
- **AF-VERIFY-02:** Verify that execution quantities must be positive integers.
- **AF-VERIFY-03:** Verify that trade timestamps order the immutable ledger.
- **AF-VERIFY-04:** Verify that duplicate trades never double count.
- **AF-VERIFY-05:** Verify that fill regression fails closed.
- **AF-VERIFY-06:** Verify that trades are keyed by order and trade id.
- **AF-VERIFY-07:** Verify that execution quantities must be positive integers.
- **AF-VERIFY-08:** Verify that trade timestamps order the immutable ledger.
- **AF-VERIFY-09:** Verify that duplicate trades never double count.
- **AF-VERIFY-10:** Verify that fill regression fails closed.
- **AF-VERIFY-11:** Verify that trades are keyed by order and trade id.
- **AF-VERIFY-12:** Verify that execution quantities must be positive integers.

### Operator interpretation
1. For this scenario, Trades are keyed by order and trade ID. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Execution quantities must be positive integers. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, Trade timestamps order the immutable ledger. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Duplicate trades never double count. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Fill regression fails closed. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Trades are keyed by order and trade ID. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Execution quantities must be positive integers. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, Trade timestamps order the immutable ledger. The caller records the returned result and does not infer a trading decision from it.

## Appendix AG — Numeric integrity

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AG-01 | Decimal conversion rejects binary floating-point input. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-01 | Tick alignment uses Decimal remainder. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-01 | Average price is volume weighted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-01 | Zero fill has no average price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-01 | Remaining quantity is derived, not trusted blindly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-02 | Decimal conversion rejects binary floating-point input. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-02 | Tick alignment uses Decimal remainder. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-02 | Average price is volume weighted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-02 | Zero fill has no average price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-02 | Remaining quantity is derived, not trusted blindly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-03 | Decimal conversion rejects binary floating-point input. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-03 | Tick alignment uses Decimal remainder. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-03 | Average price is volume weighted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-03 | Zero fill has no average price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-03 | Remaining quantity is derived, not trusted blindly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-04 | Decimal conversion rejects binary floating-point input. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-04 | Tick alignment uses Decimal remainder. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-04 | Average price is volume weighted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-04 | Zero fill has no average price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-04 | Remaining quantity is derived, not trusted blindly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-05 | Decimal conversion rejects binary floating-point input. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-05 | Tick alignment uses Decimal remainder. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-05 | Average price is volume weighted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-05 | Zero fill has no average price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-05 | Remaining quantity is derived, not trusted blindly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-06 | Decimal conversion rejects binary floating-point input. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-06 | Tick alignment uses Decimal remainder. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-06 | Average price is volume weighted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-06 | Zero fill has no average price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-06 | Remaining quantity is derived, not trusted blindly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-07 | Decimal conversion rejects binary floating-point input. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-07 | Tick alignment uses Decimal remainder. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-07 | Average price is volume weighted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-07 | Zero fill has no average price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-07 | Remaining quantity is derived, not trusted blindly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-08 | Decimal conversion rejects binary floating-point input. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-08 | Tick alignment uses Decimal remainder. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-08 | Average price is volume weighted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-08 | Zero fill has no average price. | Record deterministic `BOM` outcome and immutable snapshot. |
| AG-08 | Remaining quantity is derived, not trusted blindly. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AG-VERIFY-01:** Verify that decimal conversion rejects binary floating-point input.
- **AG-VERIFY-02:** Verify that tick alignment uses decimal remainder.
- **AG-VERIFY-03:** Verify that average price is volume weighted.
- **AG-VERIFY-04:** Verify that zero fill has no average price.
- **AG-VERIFY-05:** Verify that remaining quantity is derived, not trusted blindly.
- **AG-VERIFY-06:** Verify that decimal conversion rejects binary floating-point input.
- **AG-VERIFY-07:** Verify that tick alignment uses decimal remainder.
- **AG-VERIFY-08:** Verify that average price is volume weighted.
- **AG-VERIFY-09:** Verify that zero fill has no average price.
- **AG-VERIFY-10:** Verify that remaining quantity is derived, not trusted blindly.
- **AG-VERIFY-11:** Verify that decimal conversion rejects binary floating-point input.
- **AG-VERIFY-12:** Verify that tick alignment uses decimal remainder.

### Operator interpretation
1. For this scenario, Decimal conversion rejects binary floating-point input. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Tick alignment uses Decimal remainder. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, Average price is volume weighted. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Zero fill has no average price. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Remaining quantity is derived, not trusted blindly. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Decimal conversion rejects binary floating-point input. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Tick alignment uses Decimal remainder. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, Average price is volume weighted. The caller records the returned result and does not infer a trading decision from it.

## Appendix AH — Transport contract

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AH-01 | Transport methods have no policy decisions. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-01 | Transport maps exceptions into typed categories. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-01 | Transport preserves broker request identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-01 | Transport lookup supports idempotency reconciliation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-01 | Transport calls are mockable without network access. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-02 | Transport methods have no policy decisions. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-02 | Transport maps exceptions into typed categories. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-02 | Transport preserves broker request identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-02 | Transport lookup supports idempotency reconciliation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-02 | Transport calls are mockable without network access. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-03 | Transport methods have no policy decisions. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-03 | Transport maps exceptions into typed categories. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-03 | Transport preserves broker request identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-03 | Transport lookup supports idempotency reconciliation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-03 | Transport calls are mockable without network access. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-04 | Transport methods have no policy decisions. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-04 | Transport maps exceptions into typed categories. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-04 | Transport preserves broker request identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-04 | Transport lookup supports idempotency reconciliation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-04 | Transport calls are mockable without network access. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-05 | Transport methods have no policy decisions. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-05 | Transport maps exceptions into typed categories. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-05 | Transport preserves broker request identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-05 | Transport lookup supports idempotency reconciliation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-05 | Transport calls are mockable without network access. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-06 | Transport methods have no policy decisions. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-06 | Transport maps exceptions into typed categories. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-06 | Transport preserves broker request identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-06 | Transport lookup supports idempotency reconciliation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-06 | Transport calls are mockable without network access. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-07 | Transport methods have no policy decisions. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-07 | Transport maps exceptions into typed categories. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-07 | Transport preserves broker request identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-07 | Transport lookup supports idempotency reconciliation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-07 | Transport calls are mockable without network access. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-08 | Transport methods have no policy decisions. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-08 | Transport maps exceptions into typed categories. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-08 | Transport preserves broker request identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-08 | Transport lookup supports idempotency reconciliation. | Record deterministic `BOM` outcome and immutable snapshot. |
| AH-08 | Transport calls are mockable without network access. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AH-VERIFY-01:** Verify that transport methods have no policy decisions.
- **AH-VERIFY-02:** Verify that transport maps exceptions into typed categories.
- **AH-VERIFY-03:** Verify that transport preserves broker request identifiers.
- **AH-VERIFY-04:** Verify that transport lookup supports idempotency reconciliation.
- **AH-VERIFY-05:** Verify that transport calls are mockable without network access.
- **AH-VERIFY-06:** Verify that transport methods have no policy decisions.
- **AH-VERIFY-07:** Verify that transport maps exceptions into typed categories.
- **AH-VERIFY-08:** Verify that transport preserves broker request identifiers.
- **AH-VERIFY-09:** Verify that transport lookup supports idempotency reconciliation.
- **AH-VERIFY-10:** Verify that transport calls are mockable without network access.
- **AH-VERIFY-11:** Verify that transport methods have no policy decisions.
- **AH-VERIFY-12:** Verify that transport maps exceptions into typed categories.

### Operator interpretation
1. For this scenario, Transport methods have no policy decisions. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Transport maps exceptions into typed categories. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, Transport preserves broker request identifiers. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Transport lookup supports idempotency reconciliation. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Transport calls are mockable without network access. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Transport methods have no policy decisions. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Transport maps exceptions into typed categories. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, Transport preserves broker request identifiers. The caller records the returned result and does not infer a trading decision from it.

## Appendix AI — Retry case studies

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AI-01 | Connection reset before write is retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-01 | Read timeout after write is ambiguous. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-01 | 429 response is retryable within budget. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-01 | Invalid symbol is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-01 | Rejected margin response is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-02 | Connection reset before write is retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-02 | Read timeout after write is ambiguous. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-02 | 429 response is retryable within budget. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-02 | Invalid symbol is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-02 | Rejected margin response is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-03 | Connection reset before write is retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-03 | Read timeout after write is ambiguous. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-03 | 429 response is retryable within budget. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-03 | Invalid symbol is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-03 | Rejected margin response is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-04 | Connection reset before write is retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-04 | Read timeout after write is ambiguous. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-04 | 429 response is retryable within budget. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-04 | Invalid symbol is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-04 | Rejected margin response is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-05 | Connection reset before write is retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-05 | Read timeout after write is ambiguous. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-05 | 429 response is retryable within budget. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-05 | Invalid symbol is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-05 | Rejected margin response is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-06 | Connection reset before write is retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-06 | Read timeout after write is ambiguous. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-06 | 429 response is retryable within budget. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-06 | Invalid symbol is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-06 | Rejected margin response is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-07 | Connection reset before write is retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-07 | Read timeout after write is ambiguous. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-07 | 429 response is retryable within budget. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-07 | Invalid symbol is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-07 | Rejected margin response is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-08 | Connection reset before write is retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-08 | Read timeout after write is ambiguous. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-08 | 429 response is retryable within budget. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-08 | Invalid symbol is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |
| AI-08 | Rejected margin response is never retryable. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AI-VERIFY-01:** Verify that connection reset before write is retryable.
- **AI-VERIFY-02:** Verify that read timeout after write is ambiguous.
- **AI-VERIFY-03:** Verify that 429 response is retryable within budget.
- **AI-VERIFY-04:** Verify that invalid symbol is never retryable.
- **AI-VERIFY-05:** Verify that rejected margin response is never retryable.
- **AI-VERIFY-06:** Verify that connection reset before write is retryable.
- **AI-VERIFY-07:** Verify that read timeout after write is ambiguous.
- **AI-VERIFY-08:** Verify that 429 response is retryable within budget.
- **AI-VERIFY-09:** Verify that invalid symbol is never retryable.
- **AI-VERIFY-10:** Verify that rejected margin response is never retryable.
- **AI-VERIFY-11:** Verify that connection reset before write is retryable.
- **AI-VERIFY-12:** Verify that read timeout after write is ambiguous.

### Operator interpretation
1. For this scenario, Connection reset before write is retryable. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Read timeout after write is ambiguous. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, 429 response is retryable within budget. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Invalid symbol is never retryable. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Rejected margin response is never retryable. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Connection reset before write is retryable. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Read timeout after write is ambiguous. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, 429 response is retryable within budget. The caller records the returned result and does not infer a trading decision from it.

## Appendix AJ — Event delivery

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AJ-01 | Events are emitted after result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-01 | Event failure cannot alter a result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-01 | Events contain stable serialized payloads. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-01 | Events use correlation identifiers where supplied. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-01 | Events never contain secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-02 | Events are emitted after result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-02 | Event failure cannot alter a result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-02 | Events contain stable serialized payloads. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-02 | Events use correlation identifiers where supplied. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-02 | Events never contain secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-03 | Events are emitted after result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-03 | Event failure cannot alter a result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-03 | Events contain stable serialized payloads. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-03 | Events use correlation identifiers where supplied. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-03 | Events never contain secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-04 | Events are emitted after result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-04 | Event failure cannot alter a result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-04 | Events contain stable serialized payloads. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-04 | Events use correlation identifiers where supplied. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-04 | Events never contain secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-05 | Events are emitted after result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-05 | Event failure cannot alter a result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-05 | Events contain stable serialized payloads. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-05 | Events use correlation identifiers where supplied. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-05 | Events never contain secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-06 | Events are emitted after result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-06 | Event failure cannot alter a result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-06 | Events contain stable serialized payloads. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-06 | Events use correlation identifiers where supplied. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-06 | Events never contain secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-07 | Events are emitted after result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-07 | Event failure cannot alter a result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-07 | Events contain stable serialized payloads. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-07 | Events use correlation identifiers where supplied. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-07 | Events never contain secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-08 | Events are emitted after result sealing. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-08 | Event failure cannot alter a result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-08 | Events contain stable serialized payloads. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-08 | Events use correlation identifiers where supplied. | Record deterministic `BOM` outcome and immutable snapshot. |
| AJ-08 | Events never contain secrets. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AJ-VERIFY-01:** Verify that events are emitted after result sealing.
- **AJ-VERIFY-02:** Verify that event failure cannot alter a result.
- **AJ-VERIFY-03:** Verify that events contain stable serialized payloads.
- **AJ-VERIFY-04:** Verify that events use correlation identifiers where supplied.
- **AJ-VERIFY-05:** Verify that events never contain secrets.
- **AJ-VERIFY-06:** Verify that events are emitted after result sealing.
- **AJ-VERIFY-07:** Verify that event failure cannot alter a result.
- **AJ-VERIFY-08:** Verify that events contain stable serialized payloads.
- **AJ-VERIFY-09:** Verify that events use correlation identifiers where supplied.
- **AJ-VERIFY-10:** Verify that events never contain secrets.
- **AJ-VERIFY-11:** Verify that events are emitted after result sealing.
- **AJ-VERIFY-12:** Verify that event failure cannot alter a result.

### Operator interpretation
1. For this scenario, Events are emitted after result sealing. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Event failure cannot alter a result. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, Events contain stable serialized payloads. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Events use correlation identifiers where supplied. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Events never contain secrets. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Events are emitted after result sealing. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Event failure cannot alter a result. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, Events contain stable serialized payloads. The caller records the returned result and does not infer a trading decision from it.

## Appendix AK — Health calculations

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AK-01 | Latency uses monotonic elapsed time. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-01 | Failure rate uses a bounded operation window. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-01 | A validation failure is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-01 | A broker rejection is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-01 | Health snapshots have a capture timestamp. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-02 | Latency uses monotonic elapsed time. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-02 | Failure rate uses a bounded operation window. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-02 | A validation failure is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-02 | A broker rejection is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-02 | Health snapshots have a capture timestamp. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-03 | Latency uses monotonic elapsed time. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-03 | Failure rate uses a bounded operation window. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-03 | A validation failure is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-03 | A broker rejection is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-03 | Health snapshots have a capture timestamp. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-04 | Latency uses monotonic elapsed time. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-04 | Failure rate uses a bounded operation window. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-04 | A validation failure is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-04 | A broker rejection is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-04 | Health snapshots have a capture timestamp. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-05 | Latency uses monotonic elapsed time. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-05 | Failure rate uses a bounded operation window. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-05 | A validation failure is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-05 | A broker rejection is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-05 | Health snapshots have a capture timestamp. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-06 | Latency uses monotonic elapsed time. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-06 | Failure rate uses a bounded operation window. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-06 | A validation failure is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-06 | A broker rejection is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-06 | Health snapshots have a capture timestamp. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-07 | Latency uses monotonic elapsed time. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-07 | Failure rate uses a bounded operation window. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-07 | A validation failure is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-07 | A broker rejection is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-07 | Health snapshots have a capture timestamp. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-08 | Latency uses monotonic elapsed time. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-08 | Failure rate uses a bounded operation window. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-08 | A validation failure is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-08 | A broker rejection is not connectivity failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AK-08 | Health snapshots have a capture timestamp. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AK-VERIFY-01:** Verify that latency uses monotonic elapsed time.
- **AK-VERIFY-02:** Verify that failure rate uses a bounded operation window.
- **AK-VERIFY-03:** Verify that a validation failure is not connectivity failure.
- **AK-VERIFY-04:** Verify that a broker rejection is not connectivity failure.
- **AK-VERIFY-05:** Verify that health snapshots have a capture timestamp.
- **AK-VERIFY-06:** Verify that latency uses monotonic elapsed time.
- **AK-VERIFY-07:** Verify that failure rate uses a bounded operation window.
- **AK-VERIFY-08:** Verify that a validation failure is not connectivity failure.
- **AK-VERIFY-09:** Verify that a broker rejection is not connectivity failure.
- **AK-VERIFY-10:** Verify that health snapshots have a capture timestamp.
- **AK-VERIFY-11:** Verify that latency uses monotonic elapsed time.
- **AK-VERIFY-12:** Verify that failure rate uses a bounded operation window.

### Operator interpretation
1. For this scenario, Latency uses monotonic elapsed time. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Failure rate uses a bounded operation window. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, A validation failure is not connectivity failure. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, A broker rejection is not connectivity failure. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Health snapshots have a capture timestamp. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Latency uses monotonic elapsed time. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Failure rate uses a bounded operation window. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, A validation failure is not connectivity failure. The caller records the returned result and does not infer a trading decision from it.

## Appendix AL — Statistics calculations

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AL-01 | Counters are lifetime manager counters. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-01 | Retries count as individual attempts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-01 | Successful reconciliations count as successes. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-01 | Unique trades count once. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-01 | Snapshots never return mutable counter state. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-02 | Counters are lifetime manager counters. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-02 | Retries count as individual attempts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-02 | Successful reconciliations count as successes. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-02 | Unique trades count once. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-02 | Snapshots never return mutable counter state. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-03 | Counters are lifetime manager counters. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-03 | Retries count as individual attempts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-03 | Successful reconciliations count as successes. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-03 | Unique trades count once. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-03 | Snapshots never return mutable counter state. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-04 | Counters are lifetime manager counters. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-04 | Retries count as individual attempts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-04 | Successful reconciliations count as successes. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-04 | Unique trades count once. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-04 | Snapshots never return mutable counter state. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-05 | Counters are lifetime manager counters. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-05 | Retries count as individual attempts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-05 | Successful reconciliations count as successes. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-05 | Unique trades count once. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-05 | Snapshots never return mutable counter state. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-06 | Counters are lifetime manager counters. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-06 | Retries count as individual attempts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-06 | Successful reconciliations count as successes. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-06 | Unique trades count once. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-06 | Snapshots never return mutable counter state. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-07 | Counters are lifetime manager counters. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-07 | Retries count as individual attempts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-07 | Successful reconciliations count as successes. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-07 | Unique trades count once. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-07 | Snapshots never return mutable counter state. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-08 | Counters are lifetime manager counters. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-08 | Retries count as individual attempts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-08 | Successful reconciliations count as successes. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-08 | Unique trades count once. | Record deterministic `BOM` outcome and immutable snapshot. |
| AL-08 | Snapshots never return mutable counter state. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AL-VERIFY-01:** Verify that counters are lifetime manager counters.
- **AL-VERIFY-02:** Verify that retries count as individual attempts.
- **AL-VERIFY-03:** Verify that successful reconciliations count as successes.
- **AL-VERIFY-04:** Verify that unique trades count once.
- **AL-VERIFY-05:** Verify that snapshots never return mutable counter state.
- **AL-VERIFY-06:** Verify that counters are lifetime manager counters.
- **AL-VERIFY-07:** Verify that retries count as individual attempts.
- **AL-VERIFY-08:** Verify that successful reconciliations count as successes.
- **AL-VERIFY-09:** Verify that unique trades count once.
- **AL-VERIFY-10:** Verify that snapshots never return mutable counter state.
- **AL-VERIFY-11:** Verify that counters are lifetime manager counters.
- **AL-VERIFY-12:** Verify that retries count as individual attempts.

### Operator interpretation
1. For this scenario, Counters are lifetime manager counters. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Retries count as individual attempts. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, Successful reconciliations count as successes. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Unique trades count once. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Snapshots never return mutable counter state. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Counters are lifetime manager counters. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Retries count as individual attempts. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, Successful reconciliations count as successes. The caller records the returned result and does not infer a trading decision from it.

## Appendix AM — Exception mapping

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AM-01 | Typed errors retain safe cause classification. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-01 | Error messages are bounded and redact secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-01 | BOM codes are stable public contracts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-01 | Unmapped exceptions become internal invariant failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-01 | Stack traces are not serialized to events. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-02 | Typed errors retain safe cause classification. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-02 | Error messages are bounded and redact secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-02 | BOM codes are stable public contracts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-02 | Unmapped exceptions become internal invariant failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-02 | Stack traces are not serialized to events. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-03 | Typed errors retain safe cause classification. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-03 | Error messages are bounded and redact secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-03 | BOM codes are stable public contracts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-03 | Unmapped exceptions become internal invariant failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-03 | Stack traces are not serialized to events. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-04 | Typed errors retain safe cause classification. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-04 | Error messages are bounded and redact secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-04 | BOM codes are stable public contracts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-04 | Unmapped exceptions become internal invariant failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-04 | Stack traces are not serialized to events. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-05 | Typed errors retain safe cause classification. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-05 | Error messages are bounded and redact secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-05 | BOM codes are stable public contracts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-05 | Unmapped exceptions become internal invariant failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-05 | Stack traces are not serialized to events. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-06 | Typed errors retain safe cause classification. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-06 | Error messages are bounded and redact secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-06 | BOM codes are stable public contracts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-06 | Unmapped exceptions become internal invariant failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-06 | Stack traces are not serialized to events. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-07 | Typed errors retain safe cause classification. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-07 | Error messages are bounded and redact secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-07 | BOM codes are stable public contracts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-07 | Unmapped exceptions become internal invariant failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-07 | Stack traces are not serialized to events. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-08 | Typed errors retain safe cause classification. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-08 | Error messages are bounded and redact secrets. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-08 | BOM codes are stable public contracts. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-08 | Unmapped exceptions become internal invariant failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AM-08 | Stack traces are not serialized to events. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AM-VERIFY-01:** Verify that typed errors retain safe cause classification.
- **AM-VERIFY-02:** Verify that error messages are bounded and redact secrets.
- **AM-VERIFY-03:** Verify that bom codes are stable public contracts.
- **AM-VERIFY-04:** Verify that unmapped exceptions become internal invariant failures.
- **AM-VERIFY-05:** Verify that stack traces are not serialized to events.
- **AM-VERIFY-06:** Verify that typed errors retain safe cause classification.
- **AM-VERIFY-07:** Verify that error messages are bounded and redact secrets.
- **AM-VERIFY-08:** Verify that bom codes are stable public contracts.
- **AM-VERIFY-09:** Verify that unmapped exceptions become internal invariant failures.
- **AM-VERIFY-10:** Verify that stack traces are not serialized to events.
- **AM-VERIFY-11:** Verify that typed errors retain safe cause classification.
- **AM-VERIFY-12:** Verify that error messages are bounded and redact secrets.

### Operator interpretation
1. For this scenario, Typed errors retain safe cause classification. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Error messages are bounded and redact secrets. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, BOM codes are stable public contracts. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Unmapped exceptions become internal invariant failures. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Stack traces are not serialized to events. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Typed errors retain safe cause classification. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Error messages are bounded and redact secrets. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, BOM codes are stable public contracts. The caller records the returned result and does not infer a trading decision from it.

## Appendix AN — Security reviews

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AN-01 | No access token enters public model fields. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-01 | Raw payload retention is opt-in and redacted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-01 | Client IDs are length-limited. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-01 | Broker messages are treated as untrusted text. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-01 | Audit records contain no authorization headers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-02 | No access token enters public model fields. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-02 | Raw payload retention is opt-in and redacted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-02 | Client IDs are length-limited. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-02 | Broker messages are treated as untrusted text. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-02 | Audit records contain no authorization headers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-03 | No access token enters public model fields. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-03 | Raw payload retention is opt-in and redacted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-03 | Client IDs are length-limited. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-03 | Broker messages are treated as untrusted text. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-03 | Audit records contain no authorization headers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-04 | No access token enters public model fields. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-04 | Raw payload retention is opt-in and redacted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-04 | Client IDs are length-limited. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-04 | Broker messages are treated as untrusted text. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-04 | Audit records contain no authorization headers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-05 | No access token enters public model fields. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-05 | Raw payload retention is opt-in and redacted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-05 | Client IDs are length-limited. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-05 | Broker messages are treated as untrusted text. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-05 | Audit records contain no authorization headers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-06 | No access token enters public model fields. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-06 | Raw payload retention is opt-in and redacted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-06 | Client IDs are length-limited. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-06 | Broker messages are treated as untrusted text. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-06 | Audit records contain no authorization headers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-07 | No access token enters public model fields. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-07 | Raw payload retention is opt-in and redacted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-07 | Client IDs are length-limited. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-07 | Broker messages are treated as untrusted text. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-07 | Audit records contain no authorization headers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-08 | No access token enters public model fields. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-08 | Raw payload retention is opt-in and redacted. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-08 | Client IDs are length-limited. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-08 | Broker messages are treated as untrusted text. | Record deterministic `BOM` outcome and immutable snapshot. |
| AN-08 | Audit records contain no authorization headers. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AN-VERIFY-01:** Verify that no access token enters public model fields.
- **AN-VERIFY-02:** Verify that raw payload retention is opt-in and redacted.
- **AN-VERIFY-03:** Verify that client ids are length-limited.
- **AN-VERIFY-04:** Verify that broker messages are treated as untrusted text.
- **AN-VERIFY-05:** Verify that audit records contain no authorization headers.
- **AN-VERIFY-06:** Verify that no access token enters public model fields.
- **AN-VERIFY-07:** Verify that raw payload retention is opt-in and redacted.
- **AN-VERIFY-08:** Verify that client ids are length-limited.
- **AN-VERIFY-09:** Verify that broker messages are treated as untrusted text.
- **AN-VERIFY-10:** Verify that audit records contain no authorization headers.
- **AN-VERIFY-11:** Verify that no access token enters public model fields.
- **AN-VERIFY-12:** Verify that raw payload retention is opt-in and redacted.

### Operator interpretation
1. For this scenario, No access token enters public model fields. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Raw payload retention is opt-in and redacted. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, Client IDs are length-limited. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Broker messages are treated as untrusted text. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Audit records contain no authorization headers. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, No access token enters public model fields. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Raw payload retention is opt-in and redacted. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, Client IDs are length-limited. The caller records the returned result and does not infer a trading decision from it.

## Appendix AO — Thread safety reviews

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AO-01 | Network calls occur outside manager locks. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-01 | In-flight keys are released in finally paths. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-01 | Waiters receive sealed result or stable failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-01 | Statistics share atomic publication boundary. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-01 | No callback runs under the manager lock. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-02 | Network calls occur outside manager locks. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-02 | In-flight keys are released in finally paths. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-02 | Waiters receive sealed result or stable failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-02 | Statistics share atomic publication boundary. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-02 | No callback runs under the manager lock. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-03 | Network calls occur outside manager locks. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-03 | In-flight keys are released in finally paths. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-03 | Waiters receive sealed result or stable failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-03 | Statistics share atomic publication boundary. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-03 | No callback runs under the manager lock. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-04 | Network calls occur outside manager locks. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-04 | In-flight keys are released in finally paths. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-04 | Waiters receive sealed result or stable failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-04 | Statistics share atomic publication boundary. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-04 | No callback runs under the manager lock. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-05 | Network calls occur outside manager locks. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-05 | In-flight keys are released in finally paths. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-05 | Waiters receive sealed result or stable failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-05 | Statistics share atomic publication boundary. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-05 | No callback runs under the manager lock. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-06 | Network calls occur outside manager locks. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-06 | In-flight keys are released in finally paths. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-06 | Waiters receive sealed result or stable failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-06 | Statistics share atomic publication boundary. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-06 | No callback runs under the manager lock. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-07 | Network calls occur outside manager locks. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-07 | In-flight keys are released in finally paths. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-07 | Waiters receive sealed result or stable failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-07 | Statistics share atomic publication boundary. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-07 | No callback runs under the manager lock. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-08 | Network calls occur outside manager locks. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-08 | In-flight keys are released in finally paths. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-08 | Waiters receive sealed result or stable failure. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-08 | Statistics share atomic publication boundary. | Record deterministic `BOM` outcome and immutable snapshot. |
| AO-08 | No callback runs under the manager lock. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AO-VERIFY-01:** Verify that network calls occur outside manager locks.
- **AO-VERIFY-02:** Verify that in-flight keys are released in finally paths.
- **AO-VERIFY-03:** Verify that waiters receive sealed result or stable failure.
- **AO-VERIFY-04:** Verify that statistics share atomic publication boundary.
- **AO-VERIFY-05:** Verify that no callback runs under the manager lock.
- **AO-VERIFY-06:** Verify that network calls occur outside manager locks.
- **AO-VERIFY-07:** Verify that in-flight keys are released in finally paths.
- **AO-VERIFY-08:** Verify that waiters receive sealed result or stable failure.
- **AO-VERIFY-09:** Verify that statistics share atomic publication boundary.
- **AO-VERIFY-10:** Verify that no callback runs under the manager lock.
- **AO-VERIFY-11:** Verify that network calls occur outside manager locks.
- **AO-VERIFY-12:** Verify that in-flight keys are released in finally paths.

### Operator interpretation
1. For this scenario, Network calls occur outside manager locks. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, In-flight keys are released in finally paths. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, Waiters receive sealed result or stable failure. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Statistics share atomic publication boundary. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, No callback runs under the manager lock. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Network calls occur outside manager locks. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, In-flight keys are released in finally paths. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, Waiters receive sealed result or stable failure. The caller records the returned result and does not infer a trading decision from it.

## Appendix AP — Deterministic testing

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AP-01 | Fixed clocks make timestamps reproducible. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-01 | No-op sleepers make retries immediate. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-01 | Scripted fakes control each transport result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-01 | Stable sort keys order trade fixtures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-01 | Tests assert no call for local validation failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-02 | Fixed clocks make timestamps reproducible. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-02 | No-op sleepers make retries immediate. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-02 | Scripted fakes control each transport result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-02 | Stable sort keys order trade fixtures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-02 | Tests assert no call for local validation failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-03 | Fixed clocks make timestamps reproducible. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-03 | No-op sleepers make retries immediate. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-03 | Scripted fakes control each transport result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-03 | Stable sort keys order trade fixtures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-03 | Tests assert no call for local validation failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-04 | Fixed clocks make timestamps reproducible. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-04 | No-op sleepers make retries immediate. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-04 | Scripted fakes control each transport result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-04 | Stable sort keys order trade fixtures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-04 | Tests assert no call for local validation failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-05 | Fixed clocks make timestamps reproducible. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-05 | No-op sleepers make retries immediate. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-05 | Scripted fakes control each transport result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-05 | Stable sort keys order trade fixtures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-05 | Tests assert no call for local validation failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-06 | Fixed clocks make timestamps reproducible. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-06 | No-op sleepers make retries immediate. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-06 | Scripted fakes control each transport result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-06 | Stable sort keys order trade fixtures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-06 | Tests assert no call for local validation failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-07 | Fixed clocks make timestamps reproducible. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-07 | No-op sleepers make retries immediate. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-07 | Scripted fakes control each transport result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-07 | Stable sort keys order trade fixtures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-07 | Tests assert no call for local validation failures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-08 | Fixed clocks make timestamps reproducible. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-08 | No-op sleepers make retries immediate. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-08 | Scripted fakes control each transport result. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-08 | Stable sort keys order trade fixtures. | Record deterministic `BOM` outcome and immutable snapshot. |
| AP-08 | Tests assert no call for local validation failures. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AP-VERIFY-01:** Verify that fixed clocks make timestamps reproducible.
- **AP-VERIFY-02:** Verify that no-op sleepers make retries immediate.
- **AP-VERIFY-03:** Verify that scripted fakes control each transport result.
- **AP-VERIFY-04:** Verify that stable sort keys order trade fixtures.
- **AP-VERIFY-05:** Verify that tests assert no call for local validation failures.
- **AP-VERIFY-06:** Verify that fixed clocks make timestamps reproducible.
- **AP-VERIFY-07:** Verify that no-op sleepers make retries immediate.
- **AP-VERIFY-08:** Verify that scripted fakes control each transport result.
- **AP-VERIFY-09:** Verify that stable sort keys order trade fixtures.
- **AP-VERIFY-10:** Verify that tests assert no call for local validation failures.
- **AP-VERIFY-11:** Verify that fixed clocks make timestamps reproducible.
- **AP-VERIFY-12:** Verify that no-op sleepers make retries immediate.

### Operator interpretation
1. For this scenario, Fixed clocks make timestamps reproducible. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, No-op sleepers make retries immediate. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, Scripted fakes control each transport result. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Stable sort keys order trade fixtures. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Tests assert no call for local validation failures. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Fixed clocks make timestamps reproducible. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, No-op sleepers make retries immediate. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, Scripted fakes control each transport result. The caller records the returned result and does not infer a trading decision from it.

## Appendix AQ — Serialization reviews

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AQ-01 | Schema names identify each model family. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-01 | Envelope version is mandatory. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-01 | Decimal strings preserve exact value. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-01 | UTC offsets remain explicit. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-01 | Unknown versions fail closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-02 | Schema names identify each model family. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-02 | Envelope version is mandatory. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-02 | Decimal strings preserve exact value. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-02 | UTC offsets remain explicit. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-02 | Unknown versions fail closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-03 | Schema names identify each model family. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-03 | Envelope version is mandatory. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-03 | Decimal strings preserve exact value. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-03 | UTC offsets remain explicit. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-03 | Unknown versions fail closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-04 | Schema names identify each model family. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-04 | Envelope version is mandatory. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-04 | Decimal strings preserve exact value. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-04 | UTC offsets remain explicit. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-04 | Unknown versions fail closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-05 | Schema names identify each model family. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-05 | Envelope version is mandatory. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-05 | Decimal strings preserve exact value. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-05 | UTC offsets remain explicit. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-05 | Unknown versions fail closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-06 | Schema names identify each model family. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-06 | Envelope version is mandatory. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-06 | Decimal strings preserve exact value. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-06 | UTC offsets remain explicit. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-06 | Unknown versions fail closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-07 | Schema names identify each model family. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-07 | Envelope version is mandatory. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-07 | Decimal strings preserve exact value. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-07 | UTC offsets remain explicit. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-07 | Unknown versions fail closed. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-08 | Schema names identify each model family. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-08 | Envelope version is mandatory. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-08 | Decimal strings preserve exact value. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-08 | UTC offsets remain explicit. | Record deterministic `BOM` outcome and immutable snapshot. |
| AQ-08 | Unknown versions fail closed. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AQ-VERIFY-01:** Verify that schema names identify each model family.
- **AQ-VERIFY-02:** Verify that envelope version is mandatory.
- **AQ-VERIFY-03:** Verify that decimal strings preserve exact value.
- **AQ-VERIFY-04:** Verify that utc offsets remain explicit.
- **AQ-VERIFY-05:** Verify that unknown versions fail closed.
- **AQ-VERIFY-06:** Verify that schema names identify each model family.
- **AQ-VERIFY-07:** Verify that envelope version is mandatory.
- **AQ-VERIFY-08:** Verify that decimal strings preserve exact value.
- **AQ-VERIFY-09:** Verify that utc offsets remain explicit.
- **AQ-VERIFY-10:** Verify that unknown versions fail closed.
- **AQ-VERIFY-11:** Verify that schema names identify each model family.
- **AQ-VERIFY-12:** Verify that envelope version is mandatory.

### Operator interpretation
1. For this scenario, Schema names identify each model family. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Envelope version is mandatory. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, Decimal strings preserve exact value. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, UTC offsets remain explicit. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Unknown versions fail closed. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Schema names identify each model family. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Envelope version is mandatory. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, Decimal strings preserve exact value. The caller records the returned result and does not infer a trading decision from it.

## Appendix AR — Live runner integration

| Scenario | Contract | Expected sealed outcome |
|---|---|---|
| AR-01 | Runner injects authenticated adapter. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-01 | Runner passes projected configuration only. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-01 | Runner observes health through public snapshot. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-01 | Runner does not call Kite order primitives directly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-01 | Runner propagates correlation identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-02 | Runner injects authenticated adapter. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-02 | Runner passes projected configuration only. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-02 | Runner observes health through public snapshot. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-02 | Runner does not call Kite order primitives directly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-02 | Runner propagates correlation identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-03 | Runner injects authenticated adapter. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-03 | Runner passes projected configuration only. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-03 | Runner observes health through public snapshot. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-03 | Runner does not call Kite order primitives directly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-03 | Runner propagates correlation identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-04 | Runner injects authenticated adapter. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-04 | Runner passes projected configuration only. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-04 | Runner observes health through public snapshot. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-04 | Runner does not call Kite order primitives directly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-04 | Runner propagates correlation identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-05 | Runner injects authenticated adapter. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-05 | Runner passes projected configuration only. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-05 | Runner observes health through public snapshot. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-05 | Runner does not call Kite order primitives directly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-05 | Runner propagates correlation identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-06 | Runner injects authenticated adapter. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-06 | Runner passes projected configuration only. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-06 | Runner observes health through public snapshot. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-06 | Runner does not call Kite order primitives directly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-06 | Runner propagates correlation identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-07 | Runner injects authenticated adapter. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-07 | Runner passes projected configuration only. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-07 | Runner observes health through public snapshot. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-07 | Runner does not call Kite order primitives directly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-07 | Runner propagates correlation identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-08 | Runner injects authenticated adapter. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-08 | Runner passes projected configuration only. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-08 | Runner observes health through public snapshot. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-08 | Runner does not call Kite order primitives directly. | Record deterministic `BOM` outcome and immutable snapshot. |
| AR-08 | Runner propagates correlation identifiers. | Record deterministic `BOM` outcome and immutable snapshot. |

### Verification notes
- **AR-VERIFY-01:** Verify that runner injects authenticated adapter.
- **AR-VERIFY-02:** Verify that runner passes projected configuration only.
- **AR-VERIFY-03:** Verify that runner observes health through public snapshot.
- **AR-VERIFY-04:** Verify that runner does not call kite order primitives directly.
- **AR-VERIFY-05:** Verify that runner propagates correlation identifiers.
- **AR-VERIFY-06:** Verify that runner injects authenticated adapter.
- **AR-VERIFY-07:** Verify that runner passes projected configuration only.
- **AR-VERIFY-08:** Verify that runner observes health through public snapshot.
- **AR-VERIFY-09:** Verify that runner does not call kite order primitives directly.
- **AR-VERIFY-10:** Verify that runner propagates correlation identifiers.
- **AR-VERIFY-11:** Verify that runner injects authenticated adapter.
- **AR-VERIFY-12:** Verify that runner passes projected configuration only.

### Operator interpretation
1. For this scenario, Runner injects authenticated adapter. The caller records the returned result and does not infer a trading decision from it.
2. For this scenario, Runner passes projected configuration only. The caller records the returned result and does not infer a trading decision from it.
3. For this scenario, Runner observes health through public snapshot. The caller records the returned result and does not infer a trading decision from it.
4. For this scenario, Runner does not call Kite order primitives directly. The caller records the returned result and does not infer a trading decision from it.
5. For this scenario, Runner propagates correlation identifiers. The caller records the returned result and does not infer a trading decision from it.
6. For this scenario, Runner injects authenticated adapter. The caller records the returned result and does not infer a trading decision from it.
7. For this scenario, Runner passes projected configuration only. The caller records the returned result and does not infer a trading decision from it.
8. For this scenario, Runner observes health through public snapshot. The caller records the returned result and does not infer a trading decision from it.
