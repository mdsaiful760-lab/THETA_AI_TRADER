# Broker Client — Software Engineering Specification

| Field | Value |
|---|---|
| **Module** | `broker/base_broker.py` |
| **Document version** | 1.0.0 |
| **Status** | Draft — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-03 |

---

## 1. Purpose

`broker/base_broker.py` defines the **canonical, broker-agnostic client interface** for THETA AI TRADER.

The platform interacts with external brokers (Zerodha Kite Connect in v1 production, others in future) exclusively through this abstract contract. Concrete broker SDKs, authentication flows, rate-limit implementations, and vendor-specific payload shapes are **hidden behind implementations** in sibling modules (e.g., `broker/kite_broker.py`). Upstream consumers — the market data engine, execution layer, risk services, and orchestrators — depend only on `BaseBrokerClient`.

Today, legacy modules import `KiteConnect` directly, load API keys from environment variables, and mix transport calls with trading logic (`decision_engine.py`, pipeline scripts, root-level `market_data_engine.py`). That coupling makes the platform untestable without live broker access, blocks multi-broker support, and violates the architectural rule that **business logic must never call broker APIs directly**.

This module resolves that by providing:

1. A **pure abstract base class (ABC)** describing all broker transport capabilities required by the platform.
2. **Broker-neutral request/response types** for orders, positions, margin, and account queries.
3. **Opaque market-data payloads** returned as generic mappings so normalization remains exclusively in `market_data/market_data_adapter.py`.
4. **Explicit session and connection lifecycle** hooks without credential loading or secret management.
5. **Structured error semantics** enabling fail-closed behaviour in engines and orchestrators.

### Goals

1. Enable **dependency injection** — `MarketDataEngine`, execution services, and tests receive a `BaseBrokerClient` instance; no global broker singleton.
2. Support **multiple brokers** without modifying platform modules — add a new concrete implementation; interface stays stable.
3. Align with **`market_data/market_data_engine.py`** — the engine's injected `BrokerClient` protocol is a formal subset of this interface.
4. Align with **`core/event_bus.py`** — connection, order, and account events may be published by orchestrators wrapping broker outcomes; the interface does not publish events itself.
5. Remain **transport-only** — no strategy, intelligence, risk scoring, or order decision logic.

### Success criteria

- `market_data/market_data_engine.py` depends on `BaseBrokerClient` (or a documented protocol alias) with zero vendor imports.
- Unit tests run entirely with `FakeBrokerClient` implementations; no network or credentials required.
- Execution layer can place, modify, and cancel orders through the interface without importing vendor SDKs.
- Adding a second broker requires only a new concrete class under `broker/` — no changes to engines or adapters beyond broker-specific adapter profiles.
- All broker failures surface through stable `BROKER_CLIENT.*` error codes.

### Pipeline placement

```text
[External Auth / Session Layer]
    OAuth, token refresh, credential vault — NOT in base_broker.py
              ↓ inject BrokerSession
[broker/base_broker.py]  ← abstract contract
              ↓ implemented by
[broker/kite_broker.py]  ← Zerodha Kite Connect (v1 concrete)
[broker/<other>_broker.py]  ← future brokers
              ↓ consumed by
┌─────────────────────────────────────────────────────────────┐
│ market_data/market_data_engine.py   (market data transport) │
│ execution/execution_engine.py       (orders — future)       │
│ risk/portfolio_service.py           (positions/margin)      │
└─────────────────────────────────────────────────────────────┘
              ↓ raw payloads (market)     ↓ domain events (optional)
[market_data/market_data_adapter.py]   [core/event_bus.py]
              ↓
[MarketSnapshot → Intelligence Engines]
```

### Relationship to other modules

| Module | Relationship |
|---|---|
| `market_data/market_data_engine.py` | **Primary v1 consumer (market data).** Uses connection, instruments, quotes, LTP, historical, WebSocket subscribe, tick handlers. |
| `market_data/market_data_adapter.py` | **Downstream normalizer.** Receives broker-native payloads from engine; never imports `base_broker.py` directly in v1 — payloads pass through engine boundary. |
| `market_data/market_snapshot.py` | **Domain output.** Adapter produces snapshots; broker interface never constructs snapshot fields. |
| `core/event_bus.py` | **Optional integration.** Orchestrators may publish `system.health`, order lifecycle, or connection events based on broker callbacks; interface remains event-agnostic. |
| `core/base_engine.py` | **No direct dependency.** Analytical engines consume `MarketSnapshot`; they must not call broker APIs. |
| `kite_login.py` (legacy) | **External auth.** Produces authenticated session objects consumed by concrete broker implementations. |
| `broker/kite_broker.py` (future) | **v1 concrete implementation.** Wraps Kite Connect REST + WebSocket behind `BaseBrokerClient`. |

---

## 2. Responsibilities

`broker/base_broker.py` **is responsible for**:

| # | Responsibility | Description |
|---|---|---|
| R1 | **Abstract broker contract** | Define `BaseBrokerClient` ABC that all broker implementations must satisfy. |
| R2 | **Broker identity model** | Define `BrokerId`, `BrokerCapabilities`, and metadata describing which APIs a concrete client supports. |
| R3 | **Session model** | Define immutable `BrokerSession` — injected credentials/tokens holder without loading logic. |
| R4 | **Connection lifecycle API** | Abstract `connect`, `disconnect`, `is_connected`, connection state enumeration. |
| R5 | **Authentication state API** | Abstract session validity probes (`is_authenticated`, optional expiry metadata). |
| R6 | **Market data REST API** | Abstract instruments, quotes, LTP, OHLC, historical candles — return broker-native opaque mappings. |
| R7 | **WebSocket API** | Abstract subscribe/unsubscribe, handler registration, WebSocket connection state. |
| R8 | **Order API** | Abstract place, modify, cancel, and order status query with broker-neutral request/response types. |
| R9 | **Position API** | Abstract open positions and holdings retrieval. |
| R10 | **Margin API** | Abstract margin available, margin required for basket/order preview. |
| R11 | **Account API** | Abstract profile, funds, and account metadata retrieval. |
| R12 | **Error taxonomy** | Define `BrokerClientError` hierarchy and stable `BROKER_CLIENT.*` codes. |
| R13 | **Callback contracts** | Define handler protocols for ticks, errors, connection open/close. |
| R14 | **Thread-safety contract** | Document concurrency guarantees implementations must uphold. |
| R15 | **Logging and metrics conventions** | Standard event names and required fields for broker client implementations. |
| R16 | **Testing support** | Document `FakeBrokerClient` contract for deterministic tests. |

---

## 3. Non-Responsibilities

`broker/base_broker.py` **must not**:

| # | Non-responsibility | Rationale |
|---|---|---|
| NR1 | **Implement Zerodha/Kite or any vendor SDK** | Concrete implementations live in sibling modules. |
| NR2 | **Load API keys, secrets, or environment variables** | External auth/session injectors provide `BrokerSession`. |
| NR3 | **Perform OAuth, login, or token refresh flows** | Authentication lifecycle orchestration is external; interface exposes state probes only. |
| NR4 | **Normalize broker payloads** | Exclusive responsibility of `market_data/market_data_adapter.py`. |
| NR5 | **Construct `MarketSnapshot`** | Domain model lives in `market_data/market_snapshot.py`. |
| NR6 | **Implement trading logic** | No signal generation, strategy selection, or trade decisions. |
| NR7 | **Implement strategy logic** | Strategy engines consume snapshots, not broker APIs. |
| NR8 | **Perform market intelligence** | Regime, Greeks, confidence engines are downstream. |
| NR9 | **Risk scoring or position sizing** | Risk engines use broker data as input via orchestrators. |
| NR10 | **Publish to `EventBus` directly** | Orchestrators or wrapper services publish events. |
| NR11 | **Persist orders, fills, or snapshots** | Persistence is an external concern. |
| NR12 | **Rate-limit policy enforcement logic in ABC** | Interface may declare hooks; throttling implemented in concrete clients. |
| NR13 | **UI or dashboard rendering** | UI subscribes to events; broker interface has no UI knowledge. |
| NR14 | **Retry orchestration for engine pipelines** | Callers decide retry policy; interface may expose idempotency keys on orders. |

---

## 4. Broker Architecture

### 4.1 Layered design

```text
┌──────────────────────────────────────────────────────────────────┐
│                    Platform Consumers                             │
│  MarketDataEngine │ ExecutionEngine │ RiskService │ Orchestrator │
└────────────────────────────┬─────────────────────────────────────┘
                             │ depends on
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│              broker/base_broker.py (ABC + types)                  │
│   BaseBrokerClient │ BrokerSession │ DTOs │ Errors │ Protocols   │
└────────────────────────────┬─────────────────────────────────────┘
                             │ implements
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     KiteBrokerClient   IBKRBrokerClient   FakeBrokerClient
     (broker/kite_*)    (future)           (tests)
              │
              ▼
     Vendor SDK / REST / WebSocket
```

### 4.2 Design principles

| Principle | Application |
|---|---|
| **Interface segregation** | Market data engine uses market-data + WebSocket methods; execution uses order APIs; optional capabilities declared explicitly. |
| **Dependency inversion** | Platform depends on abstraction; concrete brokers depend on platform types. |
| **Opaque transport payloads** | Market data methods return `Mapping[str, object]` — adapter interprets vendor fields. |
| **Broker-neutral order DTOs** | Orders, positions, margin use frozen dataclasses with stable field names. |
| **Fail closed** | Invalid session, disconnected state, or auth expiry raise typed errors — never silent no-ops on mutating calls. |
| **No global mutable state** | Client instances hold connection state; no module-level singletons. |
| **Open/Closed** | New brokers extend ABC; base module unchanged. |

### 4.3 Dependency direction

```text
market_data_engine  →  broker/base_broker.py  →  stdlib + abc only
execution (future)    →  broker/base_broker.py
orchestrator          →  broker/base_broker.py
broker/kite_broker.py →  broker/base_broker.py + vendor SDK
```

`broker/base_broker.py` must not import `market_data`, `core`, or analytical engine modules.

### 4.4 Protocol alias

Downstream specs reference `BrokerClient`. Implementation provides:

```text
BrokerClient = BaseBrokerClient   # stable public alias for type hints
```

Both names refer to the same abstract contract. Breaking changes require major version bump.

---

## 5. Interface Design

### 5.1 Constants

| Symbol | Value | Description |
|---|---|---|
| `BROKER_CLIENT_VERSION` | `"1.0.0"` | Interface semantic version. |
| `DEFAULT_REST_TIMEOUT_SECONDS` | `2.0` | Recommended REST call timeout for implementations. |
| `DEFAULT_WS_RECONNECT_HINT_SECONDS` | `5.0` | Documentation default; actual reconnect owned by consumers. |

### 5.2 Enumerations

| Enum | Values (v1) | Purpose |
|---|---|---|
| `BrokerId` | `KITE`, `MOCK`, `UNKNOWN` | Identifies concrete implementation for logging and adapter profile selection. |
| `ConnectionState` | `DISCONNECTED`, `CONNECTING`, `CONNECTED`, `DEGRADED`, `RECONNECTING` | Unified transport health. |
| `WebSocketState` | `CLOSED`, `OPENING`, `OPEN`, `CLOSING`, `ERROR` | WebSocket-specific state. |
| `SessionState` | `UNAUTHENTICATED`, `AUTHENTICATED`, `EXPIRED`, `REVOKED` | Auth session validity. |
| `OrderSide` | `BUY`, `SELL` | Order direction. |
| `OrderType` | `MARKET`, `LIMIT`, `SL`, `SL_M` | Order type (extensible). |
| `ProductType` | `CNC`, `NRML`, `MIS`, `MTF` | Product classification (broker-mapped). |
| `OrderStatus` | `PENDING`, `OPEN`, `COMPLETE`, `CANCELLED`, `REJECTED`, `UNKNOWN` | Normalized order lifecycle. |
| `OrderVariety` | `REGULAR`, `AMO`, `CO`, `ICEBERG` | Order variety (broker-mapped). |
| `Exchange` | `NSE`, `BSE`, `NFO`, `BFO`, `MCX`, `CDS`, `UNKNOWN` | Exchange identifier. |
| `InstrumentKind` | `EQ`, `INDEX`, `FUT`, `CE`, `PE`, `UNKNOWN` | Instrument classification hint. |

### 5.3 Immutable session and metadata types

| Type | Description |
|---|---|
| `BrokerSession` | Frozen: `broker_id`, opaque `credentials` mapping reference, `session_id`, `authenticated_at`, optional `expires_at`. No secret logging. |
| `BrokerCapabilities` | Frozen: booleans for `websocket_ticks`, `historical_candles`, `order_placement`, `margin_preview`, `holdings`, etc. |
| `ConnectionInfo` | Frozen: `state`, `since`, `last_error_code`, `last_error_message`, `websocket_state`. |
| `BrokerClientMetadata` | Frozen: `broker_id`, `client_version`, `capabilities`, `rate_limit_hint_rps`. |

### 5.4 Handler protocols

| Protocol | Signature | Purpose |
|---|---|---|
| `TickHandler` | `(tick: Mapping[str, object]) -> None` | WebSocket tick delivery. |
| `BrokerErrorHandler` | `(error: BrokerClientError) -> None` | Transport-level errors. |
| `ConnectionHandler` | `(info: ConnectionInfo) -> None` | Connection open/close/state change. |

Handlers must be non-blocking. Implementations invoke handlers on vendor threads; consumers offload heavy work.

### 5.5 Request/response DTOs (broker-neutral)

| Type | Description |
|---|---|
| `InstrumentRequest` | Frozen: `exchange`, optional filters. |
| `QuoteRequest` | Frozen: `instrument_keys: tuple[str, ...]` (e.g., `"NSE:NIFTY 50"`). |
| `HistoricalRequest` | Frozen: `instrument_key`, `interval`, `from_ts`, `to_ts`, `continuous`. |
| `PlaceOrderRequest` | Frozen: side, type, product, quantity, price, trigger, variety, tags, idempotency_key. |
| `ModifyOrderRequest` | Frozen: `order_id`, optional quantity/price/trigger. |
| `CancelOrderRequest` | Frozen: `order_id`, optional variety. |
| `OrderQueryRequest` | Frozen: optional `order_id` filter. |
| `MarginPreviewRequest` | Frozen: list of hypothetical orders or basket id. |
| `PlaceOrderResult` | Frozen: `order_id`, `status`, `message`, `broker_order_id`, raw reference. |
| `OrderRecord` | Frozen: normalized order fields + optional raw mapping. |
| `PositionRecord` | Frozen: symbol, quantity, average_price, pnl, product, exchange. |
| `HoldingRecord` | Frozen: symbol, quantity, average_price, collateral fields. |
| `MarginSnapshot` | Frozen: available, used, total, span, exposure breakdown optional. |
| `AccountProfile` | Frozen: user_id, name, email (optional), exchanges enabled. |
| `FundsSnapshot` | Frozen: equity, commodity, available cash breakdown. |

### 5.6 Abstract base: `BaseBrokerClient`

All methods below are **abstract** unless marked *optional with default*.

| Category | Method | Returns | Description |
|---|---|---|---|
| **Metadata** | `broker_id` | `BrokerId` | Property. Stable broker identifier. |
| | `client_version` | `str` | Property. Implementation semver. |
| | `capabilities` | `BrokerCapabilities` | Property. Supported API surface. |
| | `metadata() -> BrokerClientMetadata` | Metadata snapshot. |
| **Lifecycle** | `connect() -> None` | Establish REST + WebSocket using injected session. |
| | `disconnect() -> None` | Clean shutdown; idempotent. |
| | `is_connected() -> bool` | Quick connectivity probe. |
| | `get_connection_info() -> ConnectionInfo` | Detailed connection snapshot. |
| **Auth** | `is_authenticated() -> bool` | Session valid for API calls. |
| | `get_session_state() -> SessionState` | Explicit auth state. |
| | `session_expires_at() -> datetime \| None` | Optional expiry; timezone-aware. |
| **Market data** | `fetch_instruments(request) -> tuple[Mapping, ...]` | Instrument master download. |
| | `fetch_quotes(request) -> Mapping[str, Mapping]` | Full quote batch. |
| | `fetch_ltp(request) -> Mapping[str, Mapping]` | LTP-only batch. |
| | `fetch_ohlc(request) -> Mapping[str, Mapping]` | *Optional.* OHLC batch if supported. |
| | `fetch_historical(request) -> tuple[Mapping, ...]` | Historical candles. |
| **WebSocket** | `subscribe(instrument_tokens) -> None` | Subscribe to live ticks. |
| | `unsubscribe(instrument_tokens) -> None` | Unsubscribe tokens. |
| | `get_subscribed_tokens() -> frozenset[int]` | Current subscription snapshot. |
| | `set_tick_handler(handler) -> None` | Register tick callback. |
| | `set_error_handler(handler) -> None` | Register error callback. |
| | `set_connection_handler(handler) -> None` | Register connection callback. |
| **Orders** | `place_order(request) -> PlaceOrderResult` | Submit new order. |
| | `modify_order(request) -> OrderRecord` | Modify open order. |
| | `cancel_order(request) -> OrderRecord` | Cancel order. |
| | `fetch_orders(request) -> tuple[OrderRecord, ...]` | List/query orders. |
| **Positions** | `fetch_positions() -> tuple[PositionRecord, ...]` | Open positions. |
| | `fetch_holdings() -> tuple[HoldingRecord, ...]` | *Optional.* Delivery holdings. |
| **Margin** | `fetch_margins() -> MarginSnapshot` | Account margin summary. |
| | `preview_margin(request) -> MarginSnapshot` | *Optional.* Hypothetical margin. |
| **Account** | `fetch_profile() -> AccountProfile` | User profile. |
| | `fetch_funds() -> FundsSnapshot` | *Optional.* Funds breakdown. |

Methods marked *optional* raise `BrokerCapabilityError` when unsupported unless overridden with capability flag set.

### 5.7 Exceptions

| Symbol | Description |
|---|---|
| `BrokerClientError` | Base; includes `code`, `message`, `recoverable`, `broker_id`. |
| `BrokerConfigurationError` | Invalid construction (missing session). |
| `BrokerConnectionError` | Connect/disconnect/WebSocket failure. |
| `BrokerAuthenticationError` | Session invalid or expired. |
| `BrokerRateLimitError` | Rate limit exceeded. |
| `BrokerTimeoutError` | REST or WS operation timed out. |
| `BrokerRequestError` | Invalid request parameters. |
| `BrokerOrderError` | Order rejected by broker. |
| `BrokerCapabilityError` | API not supported by this broker implementation. |

### 5.8 API stability rules

- Breaking changes to `BaseBrokerClient` abstract methods or core DTO fields require major version bump and migration notes in `CHANGELOG.md`.
- New optional methods may be added with default implementations raising `BrokerCapabilityError`.
- New optional DTO fields must use defaults for forward compatibility.

---

## 6. Authentication Lifecycle

### 6.1 Separation of concerns

Authentication **orchestration** (OAuth browser flow, daily token refresh, reading secrets from vault) lives **outside** `broker/base_broker.py`. The abstract client receives an already-constructed `BrokerSession` at initialization:

```text
[External Auth Service]
    login() → refresh() → build BrokerSession
              ↓
[Concrete BrokerClient.__init__(session: BrokerSession, ...)]
              ↓
connect() validates session with broker REST probe
```

### 6.2 `BrokerSession` fields

| Field | Required | Description |
|---|---|---|
| `broker_id` | Yes | Which implementation to use. |
| `session_id` | Yes | Opaque correlation id for logs (not the access token). |
| `authenticated_at` | Yes | Timezone-aware timestamp when session was issued. |
| `expires_at` | No | Timezone-aware expiry if known. |
| `credentials` | Yes | Opaque immutable mapping — implementation interprets; never logged. |

### 6.3 Lifecycle states

```text
[Construction with BrokerSession]
    → session_state = UNAUTHENTICATED (not yet connected)
[connect() success]
    → session_state = AUTHENTICATED
    → connection_state = CONNECTED
[broker rejects token / 403]
    → raise BrokerAuthenticationError
    → session_state = EXPIRED or REVOKED
[disconnect()]
    → connection_state = DISCONNECTED
    → WebSocket CLOSED
```

### 6.4 Session refresh

- `BaseBrokerClient` does **not** define `refresh_token()` in v1 — refresh is external.
- Implementations may expose `update_session(session: BrokerSession) -> None` as an *optional* non-abstract method for hot-swapping tokens without full reconnect (concrete detail in `kite_broker.py` spec).
- After external refresh, caller invokes `update_session` then verifies via `is_authenticated()`.

### 6.5 Fail-closed rules

| Condition | Mutating API behaviour |
|---|---|
| `not is_connected()` | Raise `BrokerConnectionError` on orders, subscribe. |
| `not is_authenticated()` | Raise `BrokerAuthenticationError` on all authenticated REST calls. |
| `session_state == EXPIRED` | Raise `BrokerAuthenticationError`; do not retry silently. |

Read-only probes (`get_connection_info`, `capabilities`) remain available when disconnected.

---

## 7. Market Data APIs

### 7.1 Design intent

Market data methods return **broker-native opaque mappings** (`Mapping[str, object]`). The interface does not interpret `last_price`, `instrument_token`, or vendor-specific keys — that is exclusively the adapter's job.

This preserves multi-broker support: each implementation returns vendor-shaped dicts; the adapter selects a normalization profile based on `BrokerId`.

### 7.2 `fetch_instruments`

| Aspect | Specification |
|---|---|
| Input | `InstrumentRequest(exchange: Exchange)` |
| Output | Tuple of instrument dicts in vendor-native shape |
| Errors | `BrokerConnectionError`, `BrokerAuthenticationError`, `BrokerTimeoutError` |
| Caching | **Not in ABC.** `MarketDataEngine` caches with TTL. |
| Idempotency | Safe to call repeatedly |

Expected vendor fields (documented for implementors, not parsed by ABC): instrument token, tradingsymbol, name, expiry, strike, instrument type, lot size, tick size, exchange.

### 7.3 `fetch_quotes` / `fetch_ltp` / `fetch_ohlc`

| Method | Input | Output |
|---|---|---|
| `fetch_quotes` | `QuoteRequest(instrument_keys)` | `Mapping[key, quote_dict]` |
| `fetch_ltp` | `QuoteRequest(instrument_keys)` | `Mapping[key, ltp_dict]` |
| `fetch_ohlc` | `QuoteRequest(instrument_keys)` | `Mapping[key, ohlc_dict]` |

Rules:

- Maximum batch size enforced by implementation (document per broker; Kite ~500 keys for quote, engine batches at 42).
- Empty `instrument_keys` raises `BrokerRequestError`.
- Missing keys omitted from result or mapped to empty dict — implementation documents behaviour; engine treats missing as stale.

### 7.4 `fetch_historical`

| Field | Description |
|---|---|
| `instrument_key` | Fully qualified key (`"NSE:NIFTY 50"`) |
| `interval` | Candle interval string (`minute`, `5minute`, `day`, etc.) |
| `from_ts` / `to_ts` | Timezone-aware datetimes |
| `continuous` | Bool for continuous futures |

Returns tuple of candle mappings (open, high, low, close, volume, timestamp in vendor format).

Optional capability — flag in `BrokerCapabilities.historical_candles`.

### 7.5 Integration with Market Data Engine

| Engine need | Broker method |
|---|---|
| Instrument master | `fetch_instruments` |
| REST quote enrichment | `fetch_quotes`, `fetch_ltp` |
| Spot/VIX during publish | `fetch_ltp` |
| Historical backfill | `fetch_historical` |
| Live ticks | WebSocket `subscribe` + `TickHandler` |

Engine never calls order or margin APIs.

---

## 8. Order APIs

### 8.1 Design intent

Order APIs use **broker-neutral frozen DTOs**. Concrete implementations translate to vendor order parameters and map responses back to `PlaceOrderResult` / `OrderRecord`.

The interface transports orders; it does **not** decide whether to trade. Execution intelligence and risk layers produce `PlaceOrderRequest` instances; orchestrators invoke the client.

### 8.2 `PlaceOrderRequest` (minimum fields)

| Field | Required | Description |
|---|---|---|
| `instrument_key` | Yes | Fully qualified tradingsymbol key. |
| `side` | Yes | `OrderSide` |
| `order_type` | Yes | `OrderType` |
| `product` | Yes | `ProductType` |
| `quantity` | Yes | Positive integer lots/units per broker rules. |
| `price` | No | Required for LIMIT orders. |
| `trigger_price` | No | Required for SL orders. |
| `variety` | No | Default `REGULAR`. |
| `validity` | No | DAY/IOC/TTL — broker mapped. |
| `tag` | No | Opaque strategy tag for audit (no logic here). |
| `idempotency_key` | No | Caller-supplied key; implementations should dedupe when supported. |
| `correlation_id` | No | Pipeline correlation for logs only. |

### 8.3 Order operations

| Method | Behaviour |
|---|---|
| `place_order` | Submit order; return `PlaceOrderResult` with broker-assigned id. |
| `modify_order` | Modify quantity/price/trigger of open order. |
| `cancel_order` | Cancel open/pending order. |
| `fetch_orders` | Return all orders or filter by id. |

### 8.4 Fail-closed rules

- Raise `BrokerOrderError` when broker rejects order (insufficient margin, invalid symbol, market closed).
- Never return `COMPLETE` status without broker confirmation.
- Rejected orders include broker message in `OrderRecord` / error.

### 8.5 Event Bus integration (orchestrator responsibility)

Orchestrators may publish:

| Topic | When |
|---|---|
| `execution.order.submitted` | After successful `place_order` |
| `execution.order.rejected` | After `BrokerOrderError` |
| `execution.order.completed` | After status poll or fill callback |

These topics are **not** defined in `base_broker.py`; documented here for cross-module alignment. Broker client remains event-agnostic.

---

## 9. Position APIs

### 9.1 `fetch_positions`

Returns open intraday and overnight positions as `PositionRecord` tuples.

| Field | Description |
|---|---|
| `instrument_key` | Qualified symbol |
| `product` | Product type |
| `quantity` | Signed net quantity |
| `average_price` | Average entry price |
| `last_price` | Optional mark price |
| `pnl` | Optional unrealized PnL |
| `exchange` | Exchange enum |
| `broker_position_id` | Optional vendor id |
| `raw` | Optional opaque vendor mapping |

### 9.2 `fetch_holdings`

Optional capability — delivery equity holdings for CNC portfolios.

Raises `BrokerCapabilityError` when not supported.

### 9.3 Usage boundaries

- Risk and APME layers consume positions via orchestrators — not directly from analytical engines during signal generation.
- Positions are **read-only** through this interface; closing positions uses order APIs.

---

## 10. Margin APIs

### 10.1 `fetch_margins`

Returns `MarginSnapshot`:

| Field | Description |
|---|---|
| `available` | Available margin for new trades |
| `used` | Margin currently utilized |
| `total` | Total margin allocated |
| `span` | Optional SPAN component |
| `exposure` | Optional exposure component |
| `commodity_available` | Optional separate segment |
| `as_of` | Timezone-aware snapshot timestamp |

### 10.2 `preview_margin`

Optional — accepts hypothetical `PlaceOrderRequest` list or basket identifier; returns projected margin impact without placing orders.

Used by risk budget and position sizing layers **before** execution — orchestrator invokes, not intelligence engines directly.

### 10.3 Fail-closed

When margin cannot be fetched, raise `BrokerConnectionError` or `BrokerAuthenticationError`. Callers must treat unavailable margin as **no new risk allocation**.

---

## 11. Account APIs

### 11.1 `fetch_profile`

Returns `AccountProfile`:

| Field | Description |
|---|---|
| `user_id` | Broker user identifier |
| `user_name` | Display name |
| `email` | Optional |
| `broker` | Broker name string |
| `exchanges` | Enabled exchanges |
| `products` | Enabled product types |

### 11.2 `fetch_funds`

Optional — equity and commodity fund breakdown when broker exposes separate ledgers.

### 11.3 Privacy

- Profile data must not be logged at INFO level.
- Email and user identifiers DEBUG only with redaction policy in concrete implementations.

---

## 12. WebSocket Lifecycle

### 12.1 Connection model

WebSocket lifecycle is owned by the concrete client but follows this state machine:

```text
CLOSED → OPENING → OPEN → CLOSING → CLOSED
                  ↓
                ERROR → (consumer reconnect policy)
```

`ConnectionState` on REST session and `WebSocketState` on tick stream may diverge (e.g., REST connected, WS degraded).

### 12.2 Subscribe flow

```text
connect()
    → open WebSocket with authenticated session
set_tick_handler(handler)
set_error_handler(handler)
set_connection_handler(handler)
subscribe(tokens=[...])
    → vendor subscribe message
    → ticks delivered to handler
unsubscribe(tokens=[...])
disconnect()
    → unsubscribe all, close socket
```

### 12.3 Tick delivery contract

| Rule | Requirement |
|---|---|
| Handler thread | Vendor callback thread — must return quickly |
| Payload shape | Opaque `Mapping[str, object]` per tick |
| Ordering | Best-effort per instrument; no cross-instrument ordering guarantee |
| Duplicates | Implementations document whether duplicates possible; engine dedupes by timestamp |
| Backpressure | Implementations drop or queue per policy; document max queue size |

### 12.4 Error and connection callbacks

- `BrokerErrorHandler` receives structured `BrokerClientError` — not bare exceptions.
- `ConnectionHandler` invoked on state transitions with `ConnectionInfo`.
- Handlers must not raise — implementations catch and log.

### 12.5 Integration with Market Data Engine

| Engine component | WebSocket usage |
|---|---|
| `ConnectionManager` | Calls `connect` / `disconnect` |
| `SubscriptionManager` | Calls `subscribe` / `unsubscribe` |
| Tick buffer | Populated from `TickHandler` |
| Reconnection | Engine policy calls `disconnect` + `connect` + resubscribe |

Engine registers handlers once at start; does not swap handlers during live session.

---

## 13. Error Handling

### 13.1 Error taxonomy

Namespace: `BROKER_CLIENT.<CATEGORY>.<DETAIL>`

| Code | Description |
|---|---|
| `BROKER_CLIENT.CONFIG.INVALID` | Bad construction parameters |
| `BROKER_CLIENT.CONFIG.MISSING_SESSION` | No session provided |
| `BROKER_CLIENT.CONNECTION.FAILED` | Unable to connect |
| `BROKER_CLIENT.CONNECTION.DISCONNECTED` | Not connected for operation |
| `BROKER_CLIENT.CONNECTION.WEBSOCKET_FAILED` | WebSocket error |
| `BROKER_CLIENT.CONNECTION.WEBSOCKET_CLOSED` | Unexpected WS close |
| `BROKER_CLIENT.AUTH.EXPIRED` | Session expired |
| `BROKER_CLIENT.AUTH.INVALID` | Invalid credentials |
| `BROKER_CLIENT.AUTH.REVOKED` | Token revoked |
| `BROKER_CLIENT.REQUEST.INVALID` | Malformed request |
| `BROKER_CLIENT.REQUEST.BATCH_TOO_LARGE` | Exceeds batch limit |
| `BROKER_CLIENT.REQUEST.TIMEOUT` | Operation timed out |
| `BROKER_CLIENT.RATE_LIMIT.EXCEEDED` | Rate limit hit |
| `BROKER_CLIENT.ORDER.REJECTED` | Broker rejected order |
| `BROKER_CLIENT.ORDER.NOT_FOUND` | Order id unknown |
| `BROKER_CLIENT.CAPABILITY.UNSUPPORTED` | API not available on broker |
| `BROKER_CLIENT.INTERNAL.UNHANDLED` | Unexpected implementation error |

### 13.2 Exception attributes

All `BrokerClientError` subclasses carry:

| Attribute | Description |
|---|---|
| `code` | Stable string from taxonomy |
| `message` | Human-readable description |
| `recoverable` | Whether caller may retry |
| `broker_id` | Originating broker |
| `cause` | Optional wrapped exception (never logged with secrets) |

### 13.3 Retry guidance (for callers, not ABC)

| Error | Retry |
|---|---|
| `BrokerRateLimitError` | Yes with backoff |
| `BrokerTimeoutError` | Yes limited retries |
| `BrokerConnectionError` | Yes after reconnect |
| `BrokerAuthenticationError` | No — refresh session externally |
| `BrokerOrderError` | No — fix request or abort |
| `BrokerRequestError` | No |

### 13.4 Exception vs return policy

| Operation | Policy |
|---|---|
| Connection failure on `connect()` | Raise `BrokerConnectionError` |
| Missing quote key | Omit from map or empty dict — document; do not raise |
| Order rejection | Raise `BrokerOrderError` with broker message |
| Unsupported API | Raise `BrokerCapabilityError` |

---

## 14. Thread Safety

| Aspect | Requirement |
|---|---|
| `connect` / `disconnect` | Safe to call from one control thread; not concurrent with each other |
| REST methods | Thread-safe or documented external synchronization; implementations use locks |
| `subscribe` / `unsubscribe` | Thread-safe |
| Handler registration | Set once before subscribe; changing handlers during live session requires disconnect |
| Tick callbacks | Invoked on vendor thread; re-entrant with REST calls discouraged |
| `get_connection_info` | Thread-safe snapshot |
| Client instance | No global singleton; one instance per session recommended |
| DTOs | Immutable after construction |

Implementations must document if REST calls from within `TickHandler` are forbidden (recommended: forbidden — use engine queue).

---

## 15. Performance Requirements

| Requirement | Target | Notes |
|---|---|---|
| `connect()` | < 3 s p95 | Includes WS handshake; broker dependent |
| `fetch_ltp` (10 keys) | < 300 ms p95 | Broker dependent |
| `fetch_quotes` (42 keys) | < 500 ms p95 | Matches engine timeout |
| `fetch_instruments` (NFO) | < 5 s p95 | Large payload; cache externally |
| Tick handler dispatch | < 0.1 ms median | Excluding consumer work |
| `place_order` | < 1 s p95 | Broker dependent |
| `fetch_margins` | < 500 ms p95 | |
| Memory per client instance | ≤ 2 MB | Excluding instrument cache |
| Rate limiting | Configurable; default 3 req/s conservative | Concrete implementation |

Benchmarks live in `tests/test_base_broker.py` against `FakeBrokerClient`.

---

## 16. Security

| Concern | Requirement |
|---|---|
| API keys / tokens | Stored only in `BrokerSession.credentials` inside concrete impl; never in ABC |
| Logging secrets | Forbidden at all levels; log `session_id` only |
| Credential injection | External only; ABC never reads env vars or files |
| TLS | Concrete implementations must use HTTPS/WSS |
| Order injection | Validate request types; no dynamic code execution |
| Tick payload logging | DEBUG only; truncated |
| Idempotency keys | Supported on orders to prevent duplicate submission |
| Least privilege | Session scoped to required permissions only (external auth concern) |
| Multi-tenant | One client instance per account session; no shared mutable credentials |

---

## 17. Testing Strategy

Tests live in `tests/test_base_broker.py` (contract tests against fakes) and broker-specific modules.

### 17.1 Test doubles

| Double | Purpose |
|---|---|
| `FakeBrokerClient` | In-memory implementation of full ABC for unit tests |
| `RecordingBrokerClient` | Wraps fake; records call sequence and arguments |
| `FailingBrokerClient` | Configurable error injection per method |

`FakeBrokerClient` lives in `tests/doubles/fake_broker_client.py` or `broker/testing/fake_broker_client.py` — not in `base_broker.py` production module.

### 17.2 Required contract tests

| Category | Cases |
|---|---|
| **Construction** | Missing session raises `BrokerConfigurationError` |
| **Connection** | connect/disconnect idempotency; `is_connected` reflects state |
| **Auth** | Unauthenticated session raises on mutating calls |
| **Market data** | fetch_instruments/quotes/ltp return mappings; empty batch rejected |
| **WebSocket** | subscribe/unsubscribe updates token set; tick handler invoked |
| **Orders** | place/modify/cancel round-trip on fake |
| **Positions/margin** | fetch returns typed records |
| **Capabilities** | Unsupported API raises `BrokerCapabilityError` |
| **Errors** | Stable error codes on simulated failures |
| **Thread safety** | Concurrent subscribe + tick delivery stress |
| **Immutability** | DTOs frozen |

### 17.3 Integration tests

- `market_data_engine` + `FakeBrokerClient`: connect, subscribe, tick, publish path.
- Optional: `kite_broker` sandbox tests behind manual CI flag — not required for ABC module.

### 17.4 Coverage target

≥ 95% line coverage on `broker/base_broker.py` (types, ABC, validation helpers).

---

## 18. Future Extension Points

| Extension | Description |
|---|---|
| **`broker/kite_broker.py`** | Zerodha Kite Connect v1 concrete implementation |
| **Async client** | `AsyncBaseBrokerClient` with same DTOs |
| **Streaming orders** | WebSocket order updates and fill callbacks |
| **Multi-leg orders** | Basket/spread order DTOs |
| **Broker-specific adapter profiles** | `BrokerId` → adapter normalization profile registry |
| **Circuit breaker** | Pluggable wrapper around any `BaseBrokerClient` |
| **OpenTelemetry** | Span per REST/WS operation with correlation_id |
| **OAuth refresh hook** | Optional `refresh_session()` when standardized across brokers |
| **Paper trading broker** | Simulated fills for backtest/live-paper |
| **gRPC broker gateway** | Remote broker proxy for distributed deployment |

Extensions must preserve broker-neutral DTOs and error taxonomy.

---

## 19. Definition of Done

The `broker/base_broker.py` module and this specification are **done** when:

### 19.1 Implementation

- [ ] `BaseBrokerClient` ABC implements all methods in §5.6.
- [ ] All DTOs and enums in §5.2–5.5 are immutable frozen dataclasses.
- [ ] Exception hierarchy with stable `BROKER_CLIENT.*` codes implemented.
- [ ] Handler protocols defined.
- [ ] `BrokerClient` public alias exported.
- [ ] No vendor SDK imports.
- [ ] No environment variable or config file loading.
- [ ] No trading, strategy, intelligence, or normalization logic.
- [ ] Google-style docstrings; Python 3.12 type hints.
- [ ] Optional methods default to `BrokerCapabilityError`.

### 19.2 Testing

- [ ] `tests/test_base_broker.py` covers §17.2 using `FakeBrokerClient`.
- [ ] Line coverage ≥ 95% on `broker/base_broker.py`.
- [ ] `market_data_engine` integration test with fake broker passes.

### 19.3 Integration

- [ ] `market_data/market_data_engine.py` types against `BaseBrokerClient` / `BrokerClient`.
- [ ] `docs/specifications/market_data_engine.md` cross-links updated.
- [ ] `CHANGELOG.md` updated.

### 19.4 Documentation

- [ ] This specification matches implementation.
- [ ] Concrete broker spec (`broker/kite_broker.md`) references this document.

### 19.5 Review checklist

- [ ] Open/Closed — new broker via new subclass only.
- [ ] No secrets in interface module.
- [ ] Market data opaque; orders/positions broker-neutral.
- [ ] Compatible with event bus orchestration patterns.

### 19.6 Sign-off

- [ ] Peer review approved.
- [ ] Specification version bumped if API changed post-review.

---

## Appendix A — Market Data Engine method mapping

| `MarketDataEngine` need | `BaseBrokerClient` method |
|---|---|
| Start connection | `connect()` |
| Stop connection | `disconnect()` |
| Health check | `is_connected()`, `get_connection_info()` |
| Load instrument master | `fetch_instruments(InstrumentRequest)` |
| REST quote enrichment | `fetch_quotes(QuoteRequest)` |
| Spot/VIX LTP | `fetch_ltp(QuoteRequest)` |
| Historical backfill | `fetch_historical(HistoricalRequest)` |
| Live ticks | `subscribe()` + `TickHandler` |
| Resubscribe after reconnect | `unsubscribe()` + `subscribe()` |
| Transport errors | `set_error_handler()` |

---

## Appendix B — Event Bus topic alignment (orchestrator-published)

The broker interface does not publish events. Orchestrators and execution wrappers should use these topic conventions when wiring to `core/event_bus.py`:

| Topic | Payload | Trigger |
|---|---|---|
| `market.snapshot.published` | `MarketSnapshot` | Market data engine (not broker directly) |
| `broker.connection.state_changed` | `ConnectionInfo` | Orchestrator on `ConnectionHandler` |
| `broker.session.expired` | `BrokerSession` metadata (no secrets) | Orchestrator on auth failure |
| `execution.order.submitted` | `PlaceOrderResult` | After `place_order` success |
| `execution.order.rejected` | `BrokerOrderError` record | After order rejection |
| `system.error` | `SystemErrorRecord` | Unrecoverable broker failures |

New topics added in `core/event_topics.py` without modifying event bus core.

---

## Appendix C — Legacy migration map

| Legacy pattern | Replacement |
|---|---|
| Direct `KiteConnect(...)` in scripts | External auth + inject `KiteBrokerClient` |
| `kite.instruments()` | `broker.fetch_instruments()` |
| `kite.quote()` / `kite.ltp()` | `broker.fetch_quotes()` / `fetch_ltp()` |
| `kite.historical_data()` | `broker.fetch_historical()` |
| `kite.place_order()` | `broker.place_order()` via execution layer |
| `kite.positions()` | `broker.fetch_positions()` |
| `kite.margins()` | `broker.fetch_margins()` |
| Env var `API_KEY` / `ACCESS_TOKEN` in engine | `BrokerSession` injected at construction |
| WebSocket tick callback inline | `set_tick_handler()` + engine buffer |

---

## Appendix D — `BrokerCapabilities` fields (v1)

| Field | Default | Description |
|---|---|---|
| `websocket_ticks` | True | Live tick stream |
| `historical_candles` | True | Historical REST |
| `order_placement` | True | Place/modify/cancel |
| `order_modification` | True | Modify open orders |
| `margin_preview` | False | Hypothetical margin |
| `holdings` | False | Delivery holdings |
| `funds_breakdown` | False | Segment-wise funds |
| `ohlc_batch` | False | OHLC REST batch |
| `amo_orders` | False | After-market orders |
| `cover_orders` | False | CO variety |

---

## Appendix E — Related documents

- `docs/specifications/market_data_engine.md`
- `docs/specifications/market_data_adapter.md`
- `docs/specifications/market_snapshot.md`
- `docs/specifications/event_bus.md`
- `docs/specifications/base_engine.md`
- `.cursor/rules/theta-ai-trader-trading-architecture.mdc`
- `.cursor/rules/theta-ai-trader-engineering-standards.mdc`
- `docs/foundation/THETA_AI_TRADER_ARCHITECTURE.md`

---

## Appendix F — Revision history

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0.0 | 2026-08-03 | THETA AI TRADER | Initial specification |
