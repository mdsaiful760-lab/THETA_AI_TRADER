# Kite WebSocket — Software Engineering Specification

| Field | Value |
|---|---|
| **Module** | `broker/kite_websocket.py` |
| **Document version** | 1.1.0 |
| **Status** | Draft — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-04 |
| **Change focus (v1.1.0)** | Configurable multi-underlying subscription without architecture change |

---

## 1. Purpose

`broker/kite_websocket.py` defines the **sole Zerodha KiteTicker streaming component** for THETA AI TRADER v1.0.

The module answers: *"How do we open, maintain, subscribe, dispatch, health-check, and tear down a Kite Connect WebSocket session for one or more configured underlyings — without hardcoding instruments, without performing OAuth, without normalizing market snapshots, and without placing orders?"*

It is the **only** module permitted to:

1. Construct and own a `KiteTicker` instance for platform streaming.
2. Manage WebSocket connection lifecycle (`connect` / `disconnect` / reconnect surface).
3. Maintain desired vs active instrument subscriptions via `SubscriptionManager`.
4. Dispatch opaque tick payloads to registered handlers and optional Event Bus topics.
5. Aggregate per-underlying and global streaming health, validation outcomes, and statistics.

It is **not** an authenticator. It is **not** a REST gateway. It is **not** a market-data normalizer. It is **not** a strategy, risk, or execution component. It is **streaming transport plumbing** for Kite ticks under a configuration-driven multi-underlying universe.

### 1.1 Architecture freeze note (unchanged)

The platform architecture remains **FROZEN** for v1.0. This module does **not**:

- Replace `BaseBrokerClient`, `KiteBrokerClient`, or `MarketDataEngine`.
- Perform OAuth / `generate_session` / token persistence (exclusive to `broker/kite_authentication.py`).
- Normalize ticks into `MarketSnapshot` (exclusive to `market_data/market_data_adapter.py`).
- Decide strike windows, ATM selection, or option-chain filtering (Market Data Engine / adapter).
- Load `.env` / YAML / merge Application Configuration files (exclusive to `config/application_configuration.py`).
- Introduce a new coordinated trading engine.

**v1.1.0 change scope:** Extend subscription, event, health, validation, and statistics contracts so they operate correctly for **multiple configured underlyings**. No other architectural decisions change.

### 1.2 Pipeline placement

```text
[config/application_configuration.py]
    MarketDataConfiguration (+ projected enabled underlyings / instrument policy)
              │
              ▼
[broker/kite_authentication.py]  →  BrokerSession (api_key, access_token)
              │
              ▼
[broker/kite_websocket.py]                         ← THIS MODULE
    ┌──────────────────────────────────────────────────────────────┐
    │ WEBSOCKET PIPELINE                                            │
    │   validate KiteWebSocketConfig (supported underlyings)       │
    │   accept configurable SubscriptionInstrument list            │
    │   connect KiteTicker with injected credentials               │
    │   SubscriptionManager.apply desired set                      │
    │   dispatch ticks → handlers / optional EventBus              │
    │   health + stats per underlying + global                     │
    └──────────────────────────────────────────────────────────────┘
              │
              ▼
[market_data/market_data_engine.py]  (consumer via BrokerClient or direct inject)
    TickBuffer → SnapshotAssembler → MarketDataAdapter → MarketSnapshot
```

### 1.3 Relationship to Kite Broker

| Concern | `kite_authentication.py` | `kite_broker.py` (REST) | `kite_websocket.py` (this module) |
|---|---|---|---|
| OAuth / token | **Owns** | Forbidden | Forbidden |
| REST quotes / orders / instruments | Forbidden | **Owns** | Forbidden (may receive pre-resolved tokens only) |
| `KiteTicker` ownership | Forbidden | May delegate | **Owns** |
| Subscribe / unsubscribe | Forbidden | May facade | **Owns implementation** |
| Tick dispatch | Forbidden | May forward | **Owns** |
| Snapshot normalization | Forbidden | Forbidden | Forbidden |

**Rule BOUNDARY-WS-001:** This module may import `kiteconnect.KiteTicker` (and tick-mode constants). It must never call REST market/order APIs or `generate_session()`.

**Rule BOUNDARY-WS-002:** Instrument tokens and trading symbols are **never hardcoded**. They arrive only via injected configuration / resolver outputs derived from Application Configuration + instrument master.

---

## 2. Goals

1. Provide a **single Kite WebSocket streaming component** for Development, Paper, and Live profiles.
2. Support **configurable underlyings** driven by Application Configuration projection.
3. Declare a **supported underlying catalog** (primary + secondary) used for validation only — not as a hardcoded subscription list.
4. Accept a **configurable instrument list** into `SubscriptionManager`.
5. Subscribe **dynamically** at runtime based on the configured universe (not compile-time constants).
6. Publish connection and tick-related events that remain correct for **multiple underlyings**.
7. Expose **health reporting** that aggregates global and per-underlying streaming health.
8. Validate config and subscription inputs for multi-underlying correctness.
9. Collect **statistics** globally and per underlying.
10. Remain **thread-safe**, **deterministic** for identical inputs, and **secret-isolating**.
11. Use immutable dataclasses at the public boundary; Google-style docstrings; ≥ 95% unit coverage.
12. **Never** hardcode instrument tokens, spot symbols, or exchange-specific quote keys inside subscription logic.

### 2.1 Success criteria

- Given `enabled_underlyings=("NIFTY","BANKNIFTY")` and a resolved instrument list for those underlyings, `connect()` + `apply_subscriptions()` yields active subscriptions covering both underlyings.
- Changing Application Configuration projection to add `SENSEX` and supplying resolved SENSEX instruments causes dynamic subscribe of the new tokens without code changes.
- Health, stats, validation, and events include per-underlying dimensions when more than one underlying is configured.
- Grep of the module finds **zero** hardcoded instrument tokens and **zero** hardcoded sole-path strings such as `"NSE:NIFTY 50"` used as the only subscription source.
- Unit coverage ≥ 95% on `broker/kite_websocket.py`.

---

## 3. Responsibilities

| # | Responsibility | Description |
|---|---|---|
| R1 | **Config validation** | Validate `KiteWebSocketConfig`, including enabled underlyings against the supported catalog. |
| R2 | **Credential binding** | Accept `api_key` + `access_token` (from `BrokerSession` / auth) without loading secrets from env. |
| R3 | **Connection lifecycle** | Connect / disconnect `KiteTicker`; surface connection state. |
| R4 | **Subscription management** | Own `SubscriptionManager` for desired vs active instrument sets. |
| R5 | **Dynamic subscribe** | Apply configurable instrument lists at runtime; re-diff on updates. |
| R6 | **Tick dispatch** | Copy opaque tick mappings; invoke handlers; optional Event Bus publish. |
| R7 | **Multi-underlying tagging** | Associate ticks and subscription records with canonical `underlying` when known. |
| R8 | **Health reporting** | Global + per-underlying health snapshots without secret leakage. |
| R9 | **Statistics** | Global + per-underlying counters (ticks, subscribe ops, errors, silence). |
| R10 | **Validation** | Validate instruments, modes, limits, and underlying membership. |
| R11 | **Error taxonomy** | Stable `KITE_WS.*` error codes. |
| R12 | **Thread safety** | Protect mutable connection/subscription state; return immutable snapshots. |
| R13 | **Reconnection surface** | Reflect SDK reconnect callbacks; do not own Market Data Engine resubscribe policy. |
| R14 | **Serialization** | Versioned JSON for public health/stats/config views (no secrets). |

---

## 4. Non-Responsibilities

| # | Non-responsibility | Rationale |
|---|---|---|
| NR1 | **OAuth / token exchange / persistence** | `kite_authentication.py`. |
| NR2 | **REST instruments / quotes / orders / positions** | `kite_broker.py` / `BaseBrokerClient`. |
| NR3 | **MarketSnapshot normalization** | `market_data_adapter.py`. |
| NR4 | **Strike window / ATM / chain filtering** | Market Data Engine + adapter. |
| NR5 | **Hardcoding instrument tokens or spot keys** | Absolute prohibition — config + resolver only. |
| NR6 | **Parsing Application Configuration files** | Accept projected `KiteWebSocketConfig` only. |
| NR7 | **Strategy / risk / execution / portfolio logic** | Downstream engines. |
| NR8 | **Owning full reconnect orchestration policy** | Market Data Engine decides when to rebuild universe and resubscribe. |
| NR9 | **Logging raw access tokens** | Security invariant SEC-WS-001. |
| NR10 | **Selecting underlyings by trading alpha** | Configuration / operator policy only. |

---

## 5. Supported Underlying Catalog

This catalog is a **validation allowlist** of canonical underlying names. It is **not** a hardcoded subscription set and **not** a source of instrument tokens.

### 5.1 Primary supported indices

| Canonical name | Role |
|---|---|
| `NIFTY` | Primary index options universe |
| `BANKNIFTY` | Primary index options universe |
| `SENSEX` | Primary index options universe |

### 5.2 Secondary supported indices

| Canonical name | Role |
|---|---|
| `FINNIFTY` | Secondary / optional index universe |
| `MIDCPNIFTY` | Secondary / optional index universe |

### 5.3 Catalog rules

| Rule | Statement |
|---|---|
| CAT-001 | Canonical names are uppercase ASCII; validation normalizes input with `strip().upper()`. |
| CAT-002 | `enabled_underlyings` must be a non-empty subset of `PRIMARY ∪ SECONDARY` unless `allow_experimental_underlyings=True` (tests / future only). |
| CAT-003 | Primary vs secondary is metadata for policy/docs/health classification — both are fully supported for subscribe/dispatch/stats when enabled. |
| CAT-004 | The module **must not** map catalog names to hardcoded `instrument_token` or `"EXCHANGE:SYMBOL"` constants. |
| CAT-005 | Spot symbol, exchange, and option tokens for each underlying come from Application Configuration projection + instrument resolution inputs. |

```text
SUPPORTED_PRIMARY_UNDERLYINGS   = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})
SUPPORTED_SECONDARY_UNDERLYINGS = frozenset({"FINNIFTY", "MIDCPNIFTY"})
SUPPORTED_UNDERLYINGS           = PRIMARY ∪ SECONDARY
```

---

## 6. Architecture (unchanged component model)

### 6.1 Component diagram

```text
┌─────────────────────────────────────────────────────────────────┐
│                    KiteWebSocketClient                           │
│                 (public facade — thread-safe)                    │
├─────────────────────────────────────────────────────────────────┤
│  ConnectionController     │  SubscriptionManager                 │
│  - KiteTicker lifecycle   │  - desired vs active instruments     │
│  - state machine          │  - configurable instrument list API  │
│  - reconnect surface      │  - per-underlying indexes            │
├───────────────────────────┴─────────────────────────────────────┤
│  TickDispatcher           │  HealthAggregator │ StatsCollector   │
│  - opaque tick copy       │  - global+per-UL  │  - global+per-UL │
│  - handler isolation      │  - no secrets     │  - counters      │
│  - optional EventBus      │                   │                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    kiteconnect.KiteTicker (private)
```

### 6.2 Design principles (unchanged)

| Principle | Application |
|---|---|
| **SDK firewall** | `KiteTicker` does not escape this module. |
| **Immutable outward boundary** | Public snapshots are frozen dataclasses / `MappingProxyType`. |
| **Config-driven universe** | Underlyings and instruments come from injected config/lists. |
| **Fail closed** | Invalid underlying / empty instrument list / disconnected subscribe → typed error. |
| **No hardcoding** | No instrument tokens or sole-path spot keys embedded in code paths. |
| **Multi-underlying first-class** | Events, health, validation, and statistics are underlying-aware. |

### 6.3 Dependency direction (unchanged)

```text
ApplicationConfiguration  →  (projection)  →  KiteWebSocketConfig
KiteAuthenticator         →  BrokerSession credentials
MarketDataEngine / Integration
        →  resolve instruments (REST via broker) 
        →  KiteWebSocketClient.set_instruments(...) / apply_subscriptions(...)
KiteWebSocketClient       →  KiteTicker
```

---

## 7. Configuration — ApplicationConfiguration projection

### 7.1 Injection contract

This module **does not** load Application Configuration. Integration Engine / Market Data Engine projects settings into `KiteWebSocketConfig` and supplies a resolved instrument list.

### 7.2 `KiteWebSocketConfig` (frozen)

| Field | Type | Description |
|---|---|---|
| `environment_profile` | `EnvironmentProfile` | Development / Paper / Production. |
| `enabled_underlyings` | `tuple[str, ...]` | Canonical underlyings to stream — **from Application Configuration projection**. |
| `tick_mode` | `KiteWebSocketTickMode` | `FULL` or `QUOTE`. |
| `max_subscriptions` | `int` | Hard cap on concurrent instrument tokens. |
| `connect_timeout_seconds` | `float` | Connect wait budget for health classification. |
| `heartbeat_silence_seconds` | `float` | Global silence threshold for DEGRADED. |
| `per_underlying_silence_seconds` | `float` | Per-underlying silence threshold. |
| `allow_experimental_underlyings` | `bool` | Default `False`; when `False`, only catalog names allowed. |
| `publish_events` | `bool` | When `True` and EventBus injected, publish `kite.ws.*` topics. |
| `runner_kind` | `str` | Audit tag (`cli`, `paper`, `live`, `test`). |
| `metadata` | `Mapping[str, str]` | Non-secret audit metadata. |

**Rule CFG-WS-001:** `enabled_underlyings` must contain ≥ 1 unique canonical name after normalization.

**Rule CFG-WS-002:** Duplicate underlyings are rejected at validation (deterministic error).

**Rule CFG-WS-003:** `max_subscriptions` must be ≥ number of instruments in the desired set when applying subscriptions.

### 7.3 Dynamic subscription from Application Configuration

```text
ApplicationConfiguration.market_data
    → project enabled_underlyings (see §7.4)
    → Market Data Engine / Integration resolves instrument master (REST)
    → build tuple[SubscriptionInstrument, ...] for enabled underlyings only
    → KiteWebSocketClient.set_instruments(instruments)
    → KiteWebSocketClient.apply_subscriptions()
```

Subscription is therefore **dynamic**: changing Application Configuration and re-resolving instruments changes the live desired set without code edits.

### 7.4 Projection expectations (no architecture change)

Application Configuration remains the sole bootstrap authority. For multi-underlying streaming, the projected config must supply `enabled_underlyings` as an ordered tuple of canonical names.

Recommended projection sources (any one is acceptable; Integration Engine chooses without changing frozen ownership):

1. Explicit multi-underlying field on `MarketDataConfiguration` when present (preferred long-term).
2. Composed list from primary `market_data.underlying` plus optional metadata / env projection such as `THETA_MARKET_UNDERLYINGS` (comma-separated), still loaded **only** by Application Configuration — never by this module.
3. Test doubles inject `KiteWebSocketConfig` directly.

**This module never reads `os.environ`.**

### 7.5 Underlying identity metadata (resolved externally)

Spot symbol / exchange for an underlying are **not** hardcoded here. They appear on each `SubscriptionInstrument` (and optional `UnderlyingDescriptor` injected for health labels) after external resolution, for example:

| Canonical | Typical spot (illustrative — NOT hardcoded constants in module) |
|---|---|
| `NIFTY` | NSE / `NIFTY 50` |
| `BANKNIFTY` | NSE / `NIFTY BANK` |
| `SENSEX` | BSE / `SENSEX` |
| `FINNIFTY` | NSE / `NIFTY FIN SERVICE` |
| `MIDCPNIFTY` | NSE / `NIFTY MID SELECT` |

Illustrative mappings may appear in Application Configuration defaults or a shared market-data preset module — **not** inside `broker/kite_websocket.py` subscription code.

---

## 8. Public API

### 8.1 Constants

| Symbol | Value | Description |
|---|---|---|
| `KITE_WEBSOCKET_VERSION` | `"1.1.0"` | Module semantic version. |
| `KITE_WEBSOCKET_SCHEMA_VERSION` | `"1.1.0"` | Public JSON schema version. |
| `PRODUCER_NAME` | `"broker.kite_websocket"` | Audit / event producer id. |
| `SUPPORTED_PRIMARY_UNDERLYINGS` | `frozenset[...]` | §5.1 |
| `SUPPORTED_SECONDARY_UNDERLYINGS` | `frozenset[...]` | §5.2 |
| `SUPPORTED_UNDERLYINGS` | union | Full allowlist |

### 8.2 Enumerations

| Enum | Values | Purpose |
|---|---|---|
| `WebSocketConnectionStatus` | `DISCONNECTED`, `CONNECTING`, `CONNECTED`, `RECONNECTING`, `DEGRADED`, `FAILED`, `CLOSED` | Transport state. |
| `KiteWebSocketTickMode` | `FULL`, `QUOTE` | Tick payload mode. |
| `SubscriptionState` | `PENDING`, `ACTIVE`, `FAILED`, `UNSUBSCRIBED` | Per-instrument state. |
| `UnderlyingSupportTier` | `PRIMARY`, `SECONDARY`, `EXPERIMENTAL` | Catalog classification. |
| `WebSocketHealthStatus` | `HEALTHY`, `DEGRADED`, `UNHEALTHY`, `UNKNOWN` | Aggregated health. |

### 8.3 Immutable models

#### 8.3.1 `SubscriptionInstrument`

Configurable instrument entry accepted by `SubscriptionManager`.

| Field | Type | Description |
|---|---|---|
| `instrument_token` | `int` | Kite instrument token (resolved externally). |
| `underlying` | `str` | Canonical underlying name. |
| `quote_key` | `str` | Broker quote key e.g. `NSE:NIFTY 50` (resolved externally). |
| `exchange` | `str` | Exchange code (resolved externally). |
| `tradingsymbol` | `str` | Trading symbol (resolved externally). |
| `instrument_kind` | `str` | e.g. `INDEX`, `CE`, `PE`, `FUT` (opaque tag). |
| `mode` | `KiteWebSocketTickMode \| None` | Optional per-instrument mode override; default = config mode. |
| `metadata` | `Mapping[str, str]` | Non-secret tags. |

**Rule INST-001:** `underlying` must be in `enabled_underlyings` when applying subscriptions.

**Rule INST-002:** `instrument_token` must be > 0.

**Rule INST-003:** Duplicate tokens in a desired set are rejected.

#### 8.3.2 `SubscriptionRecord`

| Field | Type | Description |
|---|---|---|
| `instrument_token` | `int` | Token. |
| `underlying` | `str` | Canonical underlying. |
| `quote_key` | `str` | Quote key. |
| `state` | `SubscriptionState` | Current state. |
| `mode` | `KiteWebSocketTickMode` | Effective mode. |
| `subscribed_at` | `datetime \| None` | UTC aware timestamp. |
| `last_tick_at` | `datetime \| None` | Last tick time for this token. |
| `error_code` | `str \| None` | Last error if `FAILED`. |

#### 8.3.3 `UnderlyingStreamStats`

| Field | Type | Description |
|---|---|---|
| `underlying` | `str` | Canonical name. |
| `support_tier` | `UnderlyingSupportTier` | Primary / secondary / experimental. |
| `desired_instrument_count` | `int` | Desired instruments for this underlying. |
| `active_instrument_count` | `int` | Active subscriptions. |
| `tick_count` | `int` | Ticks received since connect/reset. |
| `last_tick_at` | `datetime \| None` | Last tick for this underlying. |
| `seconds_since_last_tick` | `float \| None` | Silence metric. |
| `subscribe_success_count` | `int` | Successful subscribe batches touching this underlying. |
| `subscribe_failure_count` | `int` | Failures. |
| `error_count` | `int` | Dispatch/transport errors attributed. |

#### 8.3.4 `WebSocketStatistics`

| Field | Type | Description |
|---|---|---|
| `as_of` | `datetime` | Snapshot time. |
| `connection_status` | `WebSocketConnectionStatus` | Current status. |
| `total_desired_instruments` | `int` | Desired set size. |
| `total_active_instruments` | `int` | Active set size. |
| `total_tick_count` | `int` | Global ticks. |
| `last_tick_at` | `datetime \| None` | Global last tick. |
| `reconnect_count` | `int` | SDK reconnect observations. |
| `handler_error_count` | `int` | Isolated handler failures. |
| `enabled_underlyings` | `tuple[str, ...]` | Configured underlyings. |
| `per_underlying` | `tuple[UnderlyingStreamStats, ...]` | One entry per enabled underlying (deterministic order). |

#### 8.3.5 `UnderlyingHealthIssue` / `WebSocketHealthReport`

| Field | Type | Description |
|---|---|---|
| `report_id` | `str` | UUID. |
| `as_of` | `datetime` | Snapshot time. |
| `overall_health` | `WebSocketHealthStatus` | Aggregated. |
| `connection_status` | `WebSocketConnectionStatus` | Transport. |
| `enabled_underlyings` | `tuple[str, ...]` | Configured set. |
| `healthy_underlyings` | `tuple[str, ...]` | Underlyings currently healthy. |
| `degraded_underlyings` | `tuple[str, ...]` | Silent / partial coverage. |
| `unhealthy_underlyings` | `tuple[str, ...]` | Failed / missing subscriptions. |
| `active_subscription_count` | `int` | Active tokens. |
| `issues` | `tuple[WebSocketHealthIssue, ...]` | Structured issues (may include `underlying` field). |
| `statistics` | `WebSocketStatistics` | Embedded stats snapshot. |

#### 8.3.6 Events (immutable)

| Type | Description |
|---|---|
| `WebSocketConnectionEvent` | Status transitions; includes `enabled_underlyings`. |
| `WebSocketSubscriptionEvent` | Subscribe/unsubscribe applied; includes affected `underlying` set and token counts. |
| `WebSocketTickEvent` | Optional bus envelope: `instrument_token`, `underlying` (if known), opaque `tick` mapping copy, `received_at`. |
| `WebSocketErrorEvent` | Typed error code + redacted message; optional `underlying`. |

Suggested Event Bus topics (added to `core/event_topics.py` when wiring; this module may also use callbacks only):

| Topic | Payload |
|---|---|
| `kite.ws.connection` | `WebSocketConnectionEvent` |
| `kite.ws.subscription` | `WebSocketSubscriptionEvent` |
| `kite.ws.tick` | `WebSocketTickEvent` (optional; high volume — default off) |
| `kite.ws.error` | `WebSocketErrorEvent` |
| `kite.ws.health` | `WebSocketHealthReport` (periodic / on-demand publish) |

### 8.4 Exceptions

| Symbol | Code prefix | Description |
|---|---|---|
| `KiteWebSocketError` | `KITE_WS.*` | Base error. |
| `KiteWebSocketConfigurationError` | `KITE_WS.CONFIG.*` | Invalid config / underlyings. |
| `KiteWebSocketConnectionError` | `KITE_WS.CONNECTION.*` | Connect/disconnect failures. |
| `KiteWebSocketSubscriptionError` | `KITE_WS.SUBSCRIBE.*` | Subscription validation / apply failures. |
| `KiteWebSocketValidationError` | `KITE_WS.VALIDATION.*` | Instrument / state validation failures. |

### 8.5 Primary class: `KiteWebSocketClient`

| Method | Description |
|---|---|
| `__init__(config, *, api_key, access_token, event_bus=None, clock=None, ticker_factory=None)` | Validate config; bind credentials; construct internal components. |
| `get_status() -> WebSocketConnectionStatus` | Current status. |
| `connect() -> None` | Open KiteTicker connection. |
| `disconnect() -> None` | Close connection; clear active registry. |
| `set_instruments(instruments: Sequence[SubscriptionInstrument]) -> None` | Replace desired configurable instrument list (does not call SDK until apply). |
| `apply_subscriptions() -> WebSocketSubscriptionEvent` | Diff desired vs active; subscribe/unsubscribe via SDK. |
| `get_subscriptions() -> tuple[SubscriptionRecord, ...]` | Immutable subscription snapshot. |
| `get_instruments_for_underlying(underlying: str) -> tuple[SubscriptionRecord, ...]` | Filter by underlying. |
| `set_tick_handler(handler) -> None` | Register tick callback. |
| `set_error_handler(handler) -> None` | Register error callback. |
| `set_connection_handler(handler) -> None` | Register connection callback. |
| `get_health() -> WebSocketHealthReport` | Global + per-underlying health. |
| `get_statistics() -> WebSocketStatistics` | Global + per-underlying stats. |
| `validate() -> tuple[WebSocketValidationIssue, ...]` | Validate config + desired instruments + connection invariants. |
| `enabled_underlyings() -> tuple[str, ...]` | Normalized configured underlyings. |

### 8.6 `SubscriptionManager` (public collaborative component)

`SubscriptionManager` is an explicit component of this module (may be a public class for tests).

| Method | Description |
|---|---|
| `__init__(*, max_subscriptions: int, enabled_underlyings: Sequence[str])` | Construct with limits and underlying allowlist from config. |
| `set_instruments(instruments: Sequence[SubscriptionInstrument]) -> None` | **Accept a configurable list of instruments** (replace desired set). |
| `add_instruments(instruments: Sequence[SubscriptionInstrument]) -> None` | Append/merge into desired set with validation. |
| `remove_tokens(tokens: Sequence[int]) -> None` | Remove from desired set. |
| `clear() -> None` | Clear desired set. |
| `desired() -> tuple[SubscriptionInstrument, ...]` | Snapshot desired instruments (deterministic token order). |
| `active() -> tuple[SubscriptionRecord, ...]` | Snapshot active records. |
| `diff() -> SubscriptionDiff` | Compute subscribe/unsubscribe token sets. |
| `mark_active(tokens, *, at)` / `mark_failed(...)` / `mark_unsubscribed(...)` | State transitions after SDK calls. |
| `records_for_underlying(underlying: str) -> tuple[SubscriptionRecord, ...]` | Per-underlying view. |
| `validate_instruments(instruments) -> None` | Enforce INST-* / underlying membership / max size. |

**Rule SUB-001:** `SubscriptionManager` never calls `KiteTicker` directly — the client applies diffs.

**Rule SUB-002:** `set_instruments` accepts **any** resolved list consistent with config; it does not invent instruments.

**Rule SUB-003:** Instruments whose `underlying` is not in `enabled_underlyings` raise `KiteWebSocketValidationError` with `KITE_WS.VALIDATION.UNDERLYING_NOT_ENABLED`.

---

## 9. Connection Lifecycle (unchanged)

### 9.1 Connect

```text
connect()
    1. status = CONNECTING
    2. Construct KiteTicker(api_key, access_token) via ticker_factory
    3. Register SDK callbacks → ConnectionController / TickDispatcher
    4. ticker.connect(threaded=True)
    5. status = CONNECTED (on on_connect)
    6. Emit WebSocketConnectionEvent (includes enabled_underlyings)
```

### 9.2 Disconnect

```text
disconnect()
    1. status = CLOSING/CLOSED path
    2. ticker.close()
    3. Clear active subscription registry (desired may be retained for resubscribe)
    4. Emit WebSocketConnectionEvent
```

### 9.3 Reconnect surface (unchanged ownership)

- Reflect `on_reconnect` / `on_noreconnect` into status + health.
- **Do not** recompute option universes here — Market Data Engine decides resubscribe policy and may call `set_instruments` + `apply_subscriptions` again.

### 9.4 Threading (unchanged)

- SDK owns background thread.
- Handlers must be isolated; exceptions increment `handler_error_count` and never kill the SDK thread.
- Public getters take a lock and return frozen snapshots.

---

## 10. Multi-Underlying Subscription Flow

### 10.1 Happy path (multiple underlyings)

```text
config.enabled_underlyings = ("NIFTY", "BANKNIFTY", "SENSEX")

external resolver builds instruments:
  [NIFTY spot, NIFTY options..., BANKNIFTY spot, ..., SENSEX spot, ...]

client.set_instruments(instruments)
client.connect()
client.apply_subscriptions()
    → SubscriptionManager.diff()
    → KiteTicker.subscribe(tokens)
    → KiteTicker.set_mode(mode, tokens)
    → mark ACTIVE per token
    → publish WebSocketSubscriptionEvent{
         underlyings=("NIFTY","BANKNIFTY","SENSEX"),
         subscribed_count=N
       }
```

### 10.2 Dynamic reconfiguration

```text
# Application Configuration now enables FINNIFTY as well
config' = project(... enabled_underlyings includes FINNIFTY ...)
resolver builds updated instrument list
client.set_instruments(updated_instruments)
client.apply_subscriptions()
    → subscribe new FINNIFTY tokens
    → unsubscribe removed tokens (if any)
```

No module code changes required — only configuration + resolved instrument list.

### 10.3 Partial underlying failure

If subscribe fails for a subset of tokens:

- Mark those `SubscriptionRecord.state = FAILED`.
- Attribute failures to their `underlying` in stats/health.
- Other underlyings remain ACTIVE.
- Overall health becomes `DEGRADED` (not necessarily `UNHEALTHY`) when at least one enabled underlying still has active coverage.

### 10.4 Empty instrument list

- `set_instruments(())` is allowed to clear desired set.
- `apply_subscriptions()` while connected with empty desired set unsubscribes all.
- Starting with enabled underlyings but zero resolved instruments is a validation warning or error under fail-closed Live policy (`KITE_WS.VALIDATION.NO_INSTRUMENTS`).

---

## 11. Event Publishing (multi-underlying)

### 11.1 Requirements

Event publishing **must work for multiple configured underlyings**:

| Event | Multi-underlying behaviour |
|---|---|
| Connection | Payload includes full `enabled_underlyings` tuple. |
| Subscription | Payload includes affected underlyings and per-underlying subscribed counts in metadata. |
| Tick | Each tick event carries `underlying` when the token is in the registry; unknown tokens use `underlying=None` and increment an `unattributed_tick_count` stat. |
| Error | Optional `underlying` when attributable. |
| Health | Lists healthy/degraded/unhealthy underlying partitions. |

### 11.2 Ordering / determinism

- `enabled_underlyings` order in events matches config order.
- `per_underlying` stats ordered by config `enabled_underlyings` order.
- Token lists in subscription events sorted ascending by `instrument_token`.

### 11.3 Volume control

- Default: do **not** publish per-tick Event Bus messages in Production (`publish_events` may still emit connection/subscription/error/health).
- Tick handlers remain the primary high-frequency path for Market Data Engine.

---

## 12. Health Reporting (multi-underlying)

### 12.1 Global derivation

| Condition | `overall_health` |
|---|---|
| Disconnected / failed connect | `UNHEALTHY` |
| Connected; all enabled underlyings healthy | `HEALTHY` |
| Connected; some underlyings silent/partial/failed | `DEGRADED` |
| Connected; all enabled underlyings unhealthy | `UNHEALTHY` |
| Never connected | `UNKNOWN` |

### 12.2 Per-underlying health

An underlying is:

| State | Criteria |
|---|---|
| **Healthy** | ≥ 1 ACTIVE instrument and silence &lt; `per_underlying_silence_seconds`. |
| **Degraded** | Partial ACTIVE coverage vs desired, or silence beyond threshold, or mixed FAILED/ACTIVE. |
| **Unhealthy** | Desired count &gt; 0 and ACTIVE count == 0, or all desired FAILED. |
| **Unknown** | Desired count == 0 for that underlying (enabled but no instruments resolved yet). |

### 12.3 Issues

`WebSocketHealthIssue` fields: `issue_code`, `severity`, `message`, `underlying: str | None`, `instrument_token: int | None`.

Examples:

- `KITE_WS.HEALTH.UNDERLYING_SILENT` (`underlying="BANKNIFTY"`)
- `KITE_WS.HEALTH.UNDERLYING_UNSUBSCRIBED` (`underlying="SENSEX"`)
- `KITE_WS.HEALTH.PARTIAL_COVERAGE` (`underlying="NIFTY"`)

---

## 13. Validation (multi-underlying)

`validate()` checks:

1. Config catalog membership for each enabled underlying (CAT-*/CFG-*).
2. Every desired instrument underlying ∈ `enabled_underlyings`.
3. No duplicate tokens.
4. `len(desired) <= max_subscriptions`.
5. All tokens &gt; 0; quote keys non-empty.
6. If connected, active set ⊆ last successfully applied desired set (consistency).
7. Secondary underlyings may emit informational issues when enabled (optional, non-fatal).

Validation returns immutable issues; it does not mutate subscriptions.

---

## 14. Statistics (multi-underlying)

Statistics **must work for multiple configured underlyings**:

- Global counters always present.
- `per_underlying` contains **exactly one** `UnderlyingStreamStats` per `enabled_underlyings` entry, even if tick_count is 0.
- Tick attribution: map `instrument_token → underlying` via subscription registry; unattributed ticks counted globally only.
- `reset_statistics()` (optional public method) zeroes counters but retains configuration identity.

---

## 15. Error Codes

| Code | Meaning |
|---|---|
| `KITE_WS.CONFIG.INVALID` | Generic config failure. |
| `KITE_WS.CONFIG.UNDERLYING_REQUIRED` | Empty `enabled_underlyings`. |
| `KITE_WS.CONFIG.UNDERLYING_UNSUPPORTED` | Name not in catalog (and experimental disallowed). |
| `KITE_WS.CONFIG.UNDERLYING_DUPLICATE` | Duplicate entries. |
| `KITE_WS.CONNECTION.FAILED` | Connect failure. |
| `KITE_WS.CONNECTION.NOT_CONNECTED` | Subscribe attempted while disconnected. |
| `KITE_WS.SUBSCRIBE.LIMIT_EXCEEDED` | Desired size &gt; max. |
| `KITE_WS.SUBSCRIBE.SDK_FAILED` | KiteTicker subscribe/unsubscribe failed. |
| `KITE_WS.VALIDATION.UNDERLYING_NOT_ENABLED` | Instrument underlying not in enabled set. |
| `KITE_WS.VALIDATION.DUPLICATE_TOKEN` | Duplicate instrument token. |
| `KITE_WS.VALIDATION.INVALID_TOKEN` | Token ≤ 0. |
| `KITE_WS.VALIDATION.NO_INSTRUMENTS` | Fail-closed empty universe. |
| `KITE_WS.HEALTH.UNDERLYING_SILENT` | Per-underlying silence. |
| `KITE_WS.HEALTH.PARTIAL_COVERAGE` | Partial active coverage. |
| `KITE_WS.HEALTH.TOKEN_MISSING` | Inconsistent registry. |
| `KITE_WS.SERIALIZATION.MALFORMED` | Bad JSON. |
| `KITE_WS.SERIALIZATION.UNSUPPORTED_VERSION` | Schema mismatch. |

---

## 16. Security

| ID | Invariant |
|---|---|
| SEC-WS-001 | Never log or serialize `access_token` / `api_secret`. |
| SEC-WS-002 | `__repr__` redacts credentials. |
| SEC-WS-003 | Health/stats/events contain no secrets. |
| SEC-WS-004 | Tick payloads may include prices/OI/depth but never credentials. |

---

## 17. Thread Safety & Determinism

- One `RLock` (or equivalent) guards connection status, desired/active registries, and counters.
- Tick dispatch may update `last_tick_at` under the lock briefly; heavy handler work runs outside.
- Identical config + identical instrument lists → identical validation outcomes and deterministic ordered snapshots.
- UUID/time fields use injectable `clock` / id factory in tests.

---

## 18. Serialization

Public serializers (redacted):

- `serialize_websocket_health_report` / `deserialize_websocket_health_report`
- `serialize_websocket_statistics` / `deserialize_websocket_statistics`
- `serialize_subscription_records`

Schema version: `KITE_WEBSOCKET_SCHEMA_VERSION`.

---

## 19. Testing Requirements

### 19.1 Doubles

| Double | Role |
|---|---|
| `FakeKiteTicker` | `connect`, `close`, `subscribe`, `unsubscribe`, `set_mode`, callback hooks |
| Fixed `clock` | Deterministic silence/health |
| In-memory EventBus | Assert multi-underlying event payloads |

### 19.2 Mandatory cases

1. Config rejects unsupported underlying (`FOO`).
2. Config accepts primary + secondary mix (`NIFTY`, `FINNIFTY`).
3. `SubscriptionManager.set_instruments` accepts configurable multi-underlying list.
4. Instrument with non-enabled underlying fails validation.
5. Dynamic apply: add `BANKNIFTY` instruments → subscribe diff only new tokens.
6. Health report partitions multiple underlyings correctly.
7. Statistics expose per-underlying tick counts.
8. Events include `enabled_underlyings` / affected underlyings.
9. No hardcoded token constants in module (static grep test).
10. Concurrent `get_health` during tick dispatch is race-free.
11. Disconnect clears active set; desired retained for resubscribe.
12. Coverage ≥ 95% on `broker/kite_websocket.py`.

---

## 20. Non-Goals / Explicit Non-Changes

The following architectural decisions are **unchanged** by v1.1.0:

1. Application Configuration remains the only file/env merge authority.
2. Authentication remains exclusive to `kite_authentication.py`.
3. REST transport remains exclusive to `kite_broker.py` / `BaseBrokerClient`.
4. Snapshot normalization remains exclusive to `market_data_adapter.py`.
5. Market Data Engine remains the owner of universe computation policy and reconnect resubscribe orchestration.
6. This module does not become a coordinated analytical engine.
7. No hardcoded instrument master is embedded in this module.
8. Event Bus dispatch semantics remain owned by `core/event_bus.py`.

---

## 21. Implementation Checklist

- [ ] Implement `broker/kite_websocket.py` per this specification.
- [ ] Implement `SubscriptionManager` with configurable `set_instruments(...)`.
- [ ] Drive `enabled_underlyings` from Application Configuration projection (no env reads in module).
- [ ] Support primary: NIFTY, BANKNIFTY, SENSEX.
- [ ] Support secondary: FINNIFTY, MIDCPNIFTY.
- [ ] Zero hardcoded instrument tokens / sole-path spot keys in subscription logic.
- [ ] Multi-underlying events, health, validation, and statistics.
- [ ] `tests/test_kite_websocket.py` with ≥ 95% coverage.
- [ ] Google-style docstrings; full typing; frozen dataclasses.
- [ ] Update `CHANGELOG.md` when implementation lands.

---

## 22. Definition of Done

- [ ] All public API symbols in §8 implemented.
- [ ] Configurable underlyings verified with ≥ 2 simultaneous underlyings in tests.
- [ ] Primary and secondary catalog validation covered.
- [ ] `SubscriptionManager` accepts arbitrary resolved instrument lists constrained only by config.
- [ ] Health/stats/events/validation proven for multi-underlying scenarios.
- [ ] Architecture boundaries in §1.1 and §20 intact.
- [ ] Line coverage ≥ 95%.
- [ ] Peer review confirms no hardcoded instruments.

---

## Appendix A — Example projected config (illustrative)

```python
KiteWebSocketConfig(
    environment_profile=EnvironmentProfile.PAPER,
    enabled_underlyings=("NIFTY", "BANKNIFTY", "SENSEX"),
    tick_mode=KiteWebSocketTickMode.FULL,
    max_subscriptions=500,
    connect_timeout_seconds=10.0,
    heartbeat_silence_seconds=5.0,
    per_underlying_silence_seconds=5.0,
    allow_experimental_underlyings=False,
    publish_events=True,
    runner_kind="paper",
)
```

## Appendix B — Example configurable instruments (illustrative)

Tokens/symbols below are **examples for tests/docs only** — production values must come from instrument master resolution:

```python
instruments = (
    SubscriptionInstrument(
        instrument_token=256265,
        underlying="NIFTY",
        quote_key="NSE:NIFTY 50",
        exchange="NSE",
        tradingsymbol="NIFTY 50",
        instrument_kind="INDEX",
    ),
    SubscriptionInstrument(
        instrument_token=260105,
        underlying="BANKNIFTY",
        quote_key="NSE:NIFTY BANK",
        exchange="NSE",
        tradingsymbol="NIFTY BANK",
        instrument_kind="INDEX",
    ),
    # ... resolved option tokens for each enabled underlying ...
)
manager.set_instruments(instruments)
```

## Appendix C — Changelog

| Version | Date | Summary |
|---|---|---|
| 1.0.0 | 2026-08-04 | Initial Kite WebSocket module specification (component model, lifecycle, boundaries). |
| 1.1.0 | 2026-08-04 | Configurable multi-underlying support: primary/secondary catalog, ApplicationConfiguration-driven `enabled_underlyings`, configurable `SubscriptionManager` instrument lists, multi-underlying events/health/validation/statistics — **no other architectural changes**. |
