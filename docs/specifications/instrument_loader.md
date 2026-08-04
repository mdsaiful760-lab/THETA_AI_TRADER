# Instrument Loader — Software Engineering Specification

| Field | Value |
|---|---|
| **Module** | `broker/instrument_loader.py` |
| **Document version** | 1.0.0 |
| **Status** | Draft — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-05 |

---

## 1. Purpose

`broker/instrument_loader.py` defines the **sole instrument-master loading, validation, indexing, and catalog-serving component** for THETA AI TRADER v1.0.

The module answers a question that no other frozen module answers: *"Once Application Configuration has projected which underlyings are enabled, who downloads or loads the broker instrument master, validates and deduplicates every row, filters expired contracts, builds an immutable searchable catalog, and serves token/symbol/underlying/strike/expiry/option-type lookups — without hardcoding instrument tokens, without owning WebSocket transport, and without performing any trading logic?"*

It is the **only** module permitted to:

1. Download the broker instrument master (via an injected REST/broker client boundary — never by embedding `kiteconnect` SDK calls as a sole path).
2. Load local instrument master files (CSV / JSON fixtures / cached dumps) under a documented filesystem contract.
3. Parse broker instrument CSV rows into normalized platform records.
4. Validate records against structural and semantic rules (token, strike, expiry, option type, exchange, underlying membership).
5. Detect and resolve duplicates under a deterministic policy.
6. Remove expired option/futures contracts relative to an injected calendar clock.
7. Build an **immutable** `InstrumentCatalog` with searchable indexes.
8. Serve lookup and query APIs consumed by Market Data Streaming, Strategy Engine, Risk Engine, Order Manager, Position Manager, Portfolio Manager, APME, and System Orchestrator.
9. Project catalog rows into consumer-specific shapes (`InstrumentDescriptor` for streaming assembly, `SubscriptionInstrument` for WebSocket subscription) without those consumers re-parsing the master.

It is **not** a WebSocket client. It is **not** an authenticator. It is **not** a tick streamer. It is **not** a Market Snapshot assembler. It is **not** a strategy, risk, order, position, or portfolio component. It is **instrument catalog plumbing** — the deterministic, thread-safe, immutable source of truth for "what instruments exist and how do I find them?"

### 1.1 The gap this module fills

Multiple frozen or previously specified modules deliberately refuse to own instrument-master resolution:

| Frozen / specified module | Explicit non-responsibility |
|---|---|
| `broker/kite_websocket.py` | CAT-004 / NR2 — never maps catalog names to hardcoded tokens; receives pre-resolved `SubscriptionInstrument` lists only; never downloads the instrument master. |
| `broker/market_data_streaming.py` | BOUNDARY-MDS-008 / Appendix K — never resolves an instrument master; receives externally built `InstrumentDescriptor` records via `register_instruments()`. |
| `broker/kite_authentication.py` | Authentication/session only; never fetches instruments. |
| `broker/zerodha/kite_broker.py` | May expose `fetch_instruments` as a REST transport primitive, but does **not** own catalog validation, indexing, expiry filtering, or multi-consumer query APIs. |
| `market_data/market_data_engine.py` | Consumes resolved universes; does not own the durable searchable catalog. |
| `config/application_configuration.py` | Projects enabled underlyings and policy knobs; never connects to broker APIs and never parses instrument CSV. |

Nobody in the frozen/specified architecture currently owns:

- A single authoritative parse/validate/dedupe/filter pipeline for the broker instrument master.
- An immutable, thread-safe, in-process catalog with O(1)/O(log n) indexes by token, tradingsymbol, underlying, expiry, strike, and option type.
- Deterministic "nearest expiry / weekly / monthly / ATM / ITM / OTM / nearest strike" query helpers that every downstream engine can share.
- A stable projection contract into `InstrumentDescriptor` (streaming) and `SubscriptionInstrument` (WebSocket) so those modules never re-implement CSV parsing.
- Catalog health, load duration, duplicate counts, and expiry-filter statistics for System Orchestrator aggregation.

`broker/instrument_loader.py` closes this gap. It is the mandatory instrument-identity layer between "broker master dump exists" and "every engine that needs tokens/metadata can look them up."

### 1.2 Pipeline placement

```text
[config/application_configuration.py]
    MarketDataConfiguration / InstrumentPolicy projection
    (enabled_underlyings, exchanges, cache paths, strike/expiry policy knobs)
              │
              ▼
[broker/kite_authentication.py] ──► BrokerSession (optional; required only for live download)
              │
              ▼
[broker/zerodha/kite_broker.py] (injected InstrumentMasterClient boundary)
    fetch_instruments(exchange)  ── REST transport only
              │
              ▼
[broker/instrument_loader.py]                              ← THIS MODULE
    ┌──────────────────────────────────────────────────────────────────┐
    │ INSTRUMENT CATALOG PIPELINE                                       │
    │   load_from_broker() / load_from_file() / load_from_rows()        │
    │     → parse CSV / row mappings                                    │
    │     → normalize fields (underlying, expiry, option type, exchange)│
    │     → validate (VAL-IL-*)                                         │
    │     → detect duplicates (DUP-IL-*)                                │
    │     → filter expired contracts (EXP-IL-*)                         │
    │     → build immutable InstrumentCatalog + indexes                 │
    │     → seal CatalogStatistics / CatalogHealth                      │
    │   query / lookup APIs                                             │
    │     → by token / symbol / underlying / strike / expiry / type     │
    │     → nearest / weekly / monthly expiry                           │
    │     → ATM / ITM / OTM / nearest strike                            │
    │     → lot size                                                    │
    │   project_descriptors() / project_subscriptions()                 │
    └──────────────────────────────────────────────────────────────────┘
              │
              ├──────────────► broker/kite_websocket.py
              │                  SubscriptionInstrument[] → subscribe
              ├──────────────► broker/market_data_streaming.py
              │                  InstrumentDescriptor[] → register_instruments
              ├──────────────► Strategy / Risk / Order / Position /
              │                  Portfolio / APME / System Orchestrator
              └──────────────► diagnostics / audit / serialization dumps
```

### 1.3 Architecture freeze note

The platform architecture is **FROZEN** for v1.0. This module does **not**:

- Own `KiteTicker` or any WebSocket connection lifecycle — exclusive to `broker/kite_websocket.py` (Rule BOUNDARY-IL-001).
- Stream ticks, maintain a latest-quote book, or assemble `MarketSnapshot` objects — exclusive to `broker/market_data_streaming.py` (Rule BOUNDARY-IL-002).
- Perform OAuth, token exchange, or token persistence — exclusive to `broker/kite_authentication.py` (Rule BOUNDARY-IL-003).
- Replace `broker/zerodha/kite_broker.py` REST transport. Live download uses an **injected** `InstrumentMasterClient` protocol whose production adapter may wrap `KiteBrokerClient.fetch_instruments`; this module never embeds SDK login or order APIs (Rule BOUNDARY-IL-004).
- Redefine `market_data.market_snapshot.MarketSnapshot` or compute Greeks/IV (Rule BOUNDARY-IL-005).
- Evaluate strategies, compute trade signals, calculate position risk, size positions, manage positions, or place/modify/cancel orders (Rule BOUNDARY-IL-006).
- Load Application Configuration files, `.env` files, or environment variables directly. It accepts an already-projected `InstrumentLoaderConfig` (Rule BOUNDARY-IL-007).
- Hardcode instrument tokens, trading symbols, or sole-path spot quote keys such as `"NSE:NIFTY 50"` as the only resolution path. Spot/index identity is resolved from the loaded master (or explicitly injected override maps supplied by configuration projection — never embedded as the sole catalog) (Rule BOUNDARY-IL-008).
- Become a coordinated trading engine, gain a `run_forever` trading loop, or participate in strategy/`EngineRegistry` composition beyond providing catalog lookups (Rule BOUNDARY-IL-009).

### 1.4 Goals

1. Provide a **single instrument catalog component** that loads, validates, indexes, and serves broker instrument metadata for the entire platform.
2. Support **primary** underlyings (`NIFTY`, `BANKNIFTY`, `SENSEX`) and **secondary** underlyings (`FINNIFTY`, `MIDCPNIFTY`) without hardcoding tokens.
3. Reserve an explicit **future extension path** for NSE F&O single-stock options/futures without redesigning the catalog model.
4. Accept instrument masters from **broker download**, **local file**, and **in-memory row** sources under one validation pipeline.
5. Build an **immutable** `InstrumentCatalog` after each successful load; mutations require a new sealed catalog instance (copy-on-reload).
6. Provide **fast, thread-safe lookups** by instrument token, trading symbol, underlying, strike, expiry, option type, and exchange.
7. Provide **deterministic query helpers** for nearest/weekly/monthly expiry, ATM/ITM/OTM selection, nearest strike, closest expiry, and lot size.
8. Detect duplicates, invalid strikes, missing expiries, invalid option types, invalid exchanges, and invalid instrument tokens with stable `IL.*` error codes.
9. Expose **CatalogHealth** and **CatalogStatistics** (load duration, record counts, duplicate counts, expiry counts) for System Orchestrator.
10. Provide **versioned JSON serialization** for catalog snapshots, health, statistics, and lookup results.
11. Project into consumer contracts (`InstrumentDescriptor`, `SubscriptionInstrument`) so upstream modules never re-parse CSV.
12. Be **deterministic** — identical master rows + identical clock + identical config produce identical catalogs and query results.
13. Use **Google-style docstrings** on all public types and methods; **immutable dataclasses** (`frozen=True`) at the public boundary.
14. Reach **≥ 95% unit test coverage** on `broker/instrument_loader.py`.
15. **Never** connect a WebSocket, stream ticks, evaluate strategies, calculate risk, or place orders.

### 1.5 Success criteria

- Given a NFO instrument master CSV containing NIFTY/BANKNIFTY/SENSEX/FINNIFTY/MIDCPNIFTY rows, `InstrumentLoader.load_from_file(...)` seals an `InstrumentCatalog` whose `get_by_token(token)` returns the matching `InstrumentRecord` for every validated row retained after expiry filtering.
- `find_nearest_expiry("NIFTY", as_of=...)` returns the chronologically nearest unexpired option expiry present in the catalog for NIFTY.
- `resolve_atm_strike("NIFTY", spot=24512.0, expiry="2026-08-07")` returns `24500.0` when the catalog contains that strike grid with step 50.
- `project_descriptors(underlying="NIFTY", expiry=..., strikes_each_side=..., atm_hint=...)` returns a tuple of objects structurally compatible with `broker.market_data_streaming.InstrumentDescriptor` (same field names and semantics), including SPOT/INDEX, CE/PE pairs, and optionally FUT / VIX when configured.
- Reloading the same master bytes twice with a fixed clock yields bit-identical serialized catalogs (modulo explicit `catalog_id` / `loaded_at` fields controlled by injected factories).
- Concurrent readers calling `get_by_token` / `query_options` while a background reload swaps the sealed catalog never observe torn indexes or partially built maps.
- Grep of the module finds **zero** references to `KiteTicker`, `place_order`, strategy scoring, risk math, or position management.
- Unit coverage ≥ 95% on `broker/instrument_loader.py`.

### 1.6 Relationship to other modules

| Module | Relationship |
|---|---|
| `config/application_configuration.py` | **Upstream policy.** Projects enabled underlyings, exchanges, cache directory, and loader thresholds into `InstrumentLoaderConfig`. |
| `broker/kite_authentication.py` | **Optional upstream.** Supplies `BrokerSession` used by the injected broker client when live download is enabled. This module never imports authentication internals. |
| `broker/zerodha/kite_broker.py` | **Optional transport adapter.** Production `InstrumentMasterClient` may wrap `fetch_instruments`; this module depends on a narrow protocol, not the full broker client surface. |
| `broker/kite_websocket.py` | **Downstream consumer.** Receives `SubscriptionInstrument` projections; never parses the master itself. |
| `broker/market_data_streaming.py` | **Downstream consumer.** Receives `InstrumentDescriptor` projections via `register_instruments()`; Appendix K of that specification is fulfilled by **this** module. |
| `market_data/market_data_engine.py` | **Downstream consumer / orchestrator collaborator.** May trigger load/reload and consume catalog queries for universe selection. |
| Strategy Engine / Risk Engine / Order Manager / Position Manager / Portfolio Manager / APME | **Downstream consumers.** Read-only catalog lookups (lot size, token, expiry, strike grids); never mutate the catalog. |
| System Orchestrator | **Lifecycle / health consumer.** May call `load_*`, `reload()`, `get_health()`, `get_statistics()` during bootstrap and health aggregation. |
| `core/event_bus.py` | **Optional transport.** When `publish_events=True`, may publish `market.instruments.*` topics on successful reload. |

### 1.7 Distinction from adjacent modules

| Concern | `kite_broker.py` | `kite_websocket.py` | `market_data_streaming.py` | `instrument_loader.py` (this) |
|---|---|---|---|---|
| REST `instruments()` transport | **Yes** (primitive) | No | No | Consumes via injected client |
| Parse/validate/index full master | No | No | No | **Yes** |
| Immutable searchable catalog | No | No | No | **Yes** |
| Own `KiteTicker` | Optional elsewhere | **Yes** | No | No |
| Assemble `MarketSnapshot` | No | No | **Yes** | No |
| Hardcoded tokens | Forbidden | Forbidden | Forbidden | Forbidden |
| Output for subscription | N/A | consumes list | N/A | **projects** list |
| Output for streaming registration | N/A | N/A | consumes descriptors | **projects** descriptors |

**Rule BOUNDARY-IL-010:** This module may import shared typing/utilities and optionally import consumer **type** modules solely for projection helpers (`InstrumentDescriptor`, `SubscriptionInstrument`) when those types are available. It must never import `kiteconnect` directly, never import `KiteTicker`, and never import strategy/risk/order execution modules.

---

## 2. Responsibilities

`broker/instrument_loader.py` **is responsible for**:

| # | Responsibility | Description |
|---|---|---|
| R1 | **Broker master download** | Fetch instrument master rows for configured exchanges via an injected `InstrumentMasterClient`. |
| R2 | **Local file load** | Load instrument master dumps from configured filesystem paths (CSV required; JSON optional convenience). |
| R3 | **In-memory row load** | Accept already-fetched row mappings for tests and Integration Engine recording/replay. |
| R4 | **CSV parsing** | Parse Zerodha-style instrument CSV columns into normalized field dictionaries. |
| R5 | **Field normalization** | Normalize underlying names, exchanges, option types, expiries (`YYYY-MM-DD`), and numeric fields. |
| R6 | **Record validation** | Reject invalid tokens, strikes, expiries, option types, exchanges, and incomplete option/futures metadata. |
| R7 | **Underlying allowlist enforcement** | Retain only rows whose canonical underlying is enabled (primary/secondary/future tiers per config). |
| R8 | **Duplicate detection** | Detect duplicate `instrument_token` and duplicate `(exchange, tradingsymbol)` keys; apply deterministic resolution policy. |
| R9 | **Expiry filtering** | Drop option/futures contracts whose expiry is strictly before the configured "as of" calendar date. |
| R10 | **Immutable catalog build** | Seal an `InstrumentCatalog` containing frozen `InstrumentRecord` tuples and frozen index structures. |
| R11 | **Index construction** | Build indexes by token, tradingsymbol, underlying, expiry, strike, option type, exchange, and role. |
| R12 | **Lookup APIs** | Serve O(1) / O(log n) lookups returning `LookupResult`. |
| R13 | **Query APIs** | Serve nearest/weekly/monthly expiry, ATM/ITM/OTM, nearest strike, closest expiry, and lot-size queries. |
| R14 | **Consumer projection** | Project catalog subsets into streaming descriptors and WebSocket subscription instruments. |
| R15 | **Cache persistence (optional)** | Optionally write/read a versioned local cache of the sealed catalog to avoid re-download within a trading day. |
| R16 | **Health reporting** | Expose `CatalogHealth` with load state, issues, and freshness. |
| R17 | **Statistics** | Expose `CatalogStatistics` with counts, timings, duplicate/expiry discard metrics. |
| R18 | **Error taxonomy** | Raise typed errors with stable `IL.*` codes. |
| R19 | **Thread safety** | Protect reload swaps with well-scoped locks; readers always observe a fully sealed catalog. |
| R20 | **Determinism** | Guarantee identical inputs + clock + config → identical catalogs and query results. |
| R21 | **Serialization** | Provide versioned JSON serialization for catalog, health, statistics, and lookup results. |
| R22 | **Lifecycle management** | Implement `CREATED` → `LOADING` → `READY` → `RELOADING` / `DEGRADED` / `CLOSED` state machine. |
| R23 | **Configuration validation** | Validate `InstrumentLoaderConfig` before any load begins. |
| R24 | **Audit metadata** | Record source URI/path, schema version, load correlation IDs, and row discard reasons for diagnostics. |
| R25 | **Future equity F&O readiness** | Model instrument records so NSE F&O stocks can be enabled later without schema redesign. |

---

## 3. Non-Responsibilities

`broker/instrument_loader.py` **must not**:

| # | Non-responsibility | Owner instead |
|---|---|---|
| NR1 | **Connect or manage WebSocket sessions** | `broker/kite_websocket.py` |
| NR2 | **Stream ticks or maintain latest quotes** | `broker/market_data_streaming.py` |
| NR3 | **Assemble or validate `MarketSnapshot`** | `market_data/market_snapshot.py` + streaming module |
| NR4 | **Perform OAuth / token persistence** | `broker/kite_authentication.py` |
| NR5 | **Place, modify, or cancel orders** | Order Manager / Execution Engine / `kite_broker.py` |
| NR6 | **Evaluate strategies or score signals** | Strategy Engine / Strategy Evaluation Engine |
| NR7 | **Calculate portfolio or position risk** | Risk Engine / Portfolio Manager / APME |
| NR8 | **Manage open positions or hedges** | Position Manager / APME |
| NR9 | **Hardcode instrument tokens or sole-path spot keys** | Absolute prohibition |
| NR10 | **Compute Greeks or Implied Volatility** | Option Greeks Engine |
| NR11 | **Decide strategy strike-window policy as trading logic** | Market Data Engine / Strategy policy; this module only applies configured query parameters |
| NR12 | **Load `.env` / YAML configuration files** | Application Configuration |
| NR13 | **Own broker session secrets** | Authentication / secret providers |
| NR14 | **Subscribe/unsubscribe WebSocket tokens** | `kite_websocket.SubscriptionManager` |
| NR15 | **Provide live LTP/quote values** | Broker REST / streaming; catalog is static metadata only |
| NR16 | **Mutate a sealed catalog in place** | Reload builds a new sealed catalog and atomically swaps the reference |
| NR17 | **Scrape unofficial HTML sources for instruments** | Only broker REST + local files + injected rows |
| NR18 | **Act as System Orchestrator** | Orchestrator calls this module; this module does not orchestrate peers |

---

## 4. Supported Underlying Catalog

This catalog is a **validation allowlist** of canonical underlying names for v1.0 index derivatives. It is **not** a hardcoded token table and **not** a subscription list.

### 4.1 Primary underlyings

| Underlying | Tier | Notes |
|---|---|---|
| `NIFTY` | PRIMARY | NSE index options/futures |
| `BANKNIFTY` | PRIMARY | NSE bank index options/futures |
| `SENSEX` | PRIMARY | BSE index options/futures |

### 4.2 Secondary underlyings

| Underlying | Tier | Notes |
|---|---|---|
| `FINNIFTY` | SECONDARY | NSE financial index derivatives |
| `MIDCPNIFTY` | SECONDARY | NSE midcap index derivatives |

### 4.3 Future tier — NSE F&O stocks

| Underlying pattern | Tier | v1.0 status |
|---|---|---|
| NSE F&O single-stock names (e.g. `RELIANCE`, `TCS`, …) | FUTURE / EQUITY_FO | **Schema-ready, disabled by default.** Enabled only when `allow_equity_fo=True` and names appear in `enabled_equity_underlyings`. |

### 4.4 Catalog constants

```python
SUPPORTED_PRIMARY_UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})
SUPPORTED_SECONDARY_UNDERLYINGS = frozenset({"FINNIFTY", "MIDCPNIFTY"})
SUPPORTED_INDEX_UNDERLYINGS = (
    SUPPORTED_PRIMARY_UNDERLYINGS | SUPPORTED_SECONDARY_UNDERLYINGS
)
# Equity F&O names are NEVER hardcoded here; they arrive via config projection.
```

### 4.5 Catalog rules

| Rule ID | Statement |
|---|---|
| CAT-IL-001 | Canonical names are uppercase ASCII without spaces. |
| CAT-IL-002 | `normalize_underlying_name()` strips and uppercases; empty names are rejected. |
| CAT-IL-003 | Primary/secondary membership must remain identical to `broker.kite_websocket` and `broker.market_data_streaming` catalogs (contract test `test_underlying_catalog_parity`). |
| CAT-IL-004 | This module must not map catalog names to hardcoded `instrument_token` integers. |
| CAT-IL-005 | Spot/index rows are discovered from the loaded master (or from an injected `spot_overrides` map supplied by configuration — never as the sole embedded dictionary of tokens). |
| CAT-IL-006 | When `allow_equity_fo=False`, equity F&O rows are discarded with statistics increment `discarded_equity_fo_count`. |
| CAT-IL-007 | Experimental underlyings outside the catalogs are accepted only when `allow_experimental_underlyings=True`. |

---

## 5. Architecture

### 5.1 Component diagram

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                     InstrumentLoader (facade)                            │
│  load_from_broker / load_from_file / load_from_rows / reload / close     │
│  get_catalog / get_health / get_statistics / query_* / project_*         │
└───────────────┬─────────────────────────────┬────────────────────────────┘
                │                             │
                ▼                             ▼
┌───────────────────────────┐   ┌─────────────────────────────────────────┐
│ InstrumentMasterClient    │   │ LocalInstrumentFileStore                │
│ (protocol; injected)      │   │ CSV/JSON path IO + optional cache       │
└───────────────────────────┘   └─────────────────────────────────────────┘
                │                             │
                └──────────────┬──────────────┘
                               ▼
                ┌──────────────────────────────┐
                │ InstrumentCsvParser          │
                │ column map → raw row dicts   │
                └──────────────┬───────────────┘
                               ▼
                ┌──────────────────────────────┐
                │ InstrumentNormalizer         │
                │ underlying/expiry/type/etc.  │
                └──────────────┬───────────────┘
                               ▼
                ┌──────────────────────────────┐
                │ InstrumentRecordValidator    │
                │ VAL-IL-* rules               │
                └──────────────┬───────────────┘
                               ▼
                ┌──────────────────────────────┐
                │ DuplicateResolver            │
                │ DUP-IL-* policy              │
                └──────────────┬───────────────┘
                               ▼
                ┌──────────────────────────────┐
                │ ExpiryFilter                 │
                │ EXP-IL-* calendar rules      │
                └──────────────┬───────────────┘
                               ▼
                ┌──────────────────────────────┐
                │ CatalogIndexBuilder          │
                │ token/symbol/underlying/...  │
                └──────────────┬───────────────┘
                               ▼
                ┌──────────────────────────────┐
                │ InstrumentCatalog (frozen)   │
                │ records + indexes + meta     │
                └──────────────────────────────┘
```

### 5.2 Design principles

| Principle | Meaning |
|---|---|
| **Single authority** | One module owns master parse/validate/index for the platform. |
| **Immutability after seal** | A published catalog never mutates; reload swaps references atomically. |
| **Config-driven universe** | Enabled underlyings/exchanges arrive via `InstrumentLoaderConfig`. |
| **No hardcoded tokens** | Tokens come only from loaded master rows or explicit injected overrides. |
| **Fail closed** | Invalid config / unreadable file / empty retained set (when required) → typed error. |
| **Deterministic** | Stable sort keys; stable duplicate winners; stable index iteration order. |
| **Thread-safe reads** | Readers never block on parse; reload uses atomic reference swap. |
| **Projection, not coupling** | Consumers receive projected DTOs; they do not depend on CSV columns. |
| **Boundary discipline** | No WebSocket, no ticks, no orders, no strategy/risk math. |

### 5.3 Collaborative public types

| Type | Role |
|---|---|
| `InstrumentRecord` | One validated, normalized instrument row. |
| `InstrumentCatalog` | Sealed immutable catalog + indexes. |
| `CatalogHealth` | Health snapshot for orchestrator aggregation. |
| `CatalogStatistics` | Counters and timings for the last load/reload. |
| `LookupResult` | Uniform lookup/query envelope (hit/miss + records + diagnostics). |
| `InstrumentLoaderConfig` | Frozen configuration projection. |
| `InstrumentLoader` | Lifecycle facade and query surface. |

### 5.4 Internal collaborative components

| Component | Responsibility |
|---|---|
| `InstrumentCsvParser` | CSV dialect + column mapping. |
| `InstrumentNormalizer` | Field normalization pure functions. |
| `InstrumentRecordValidator` | Structural/semantic validation. |
| `DuplicateResolver` | Deterministic duplicate collapse. |
| `ExpiryFilter` | Drop expired derivatives. |
| `CatalogIndexBuilder` | Build frozen indexes. |
| `CatalogProjector` | Project descriptors/subscriptions. |
| `LocalInstrumentFileStore` | Filesystem read/write + cache. |

**Rule ARCH-IL-001:** Engines communicate through sealed catalogs and projection helpers. No engine may reach into parser internals.

**Rule ARCH-IL-002:** The loader may retain at most one "current" sealed catalog reference plus an optional previous catalog for diagnostics; it must not accumulate unbounded historical catalogs in memory.

---

## 6. Dependency Direction

```text
ApplicationConfiguration  →  InstrumentLoaderConfig
BrokerSession (optional)  →  InstrumentMasterClient adapter  →  InstrumentLoader
Local CSV/JSON files      →  InstrumentLoader
InstrumentLoader          →  InstrumentCatalog (immutable)
InstrumentCatalog         →  MarketDataStreamingEngine.register_instruments (via projection)
InstrumentCatalog         →  KiteWebSocketClient.set_instruments (via projection)
InstrumentCatalog         →  Strategy / Risk / Order / Position / Portfolio / APME (lookups)
InstrumentLoader          →  SystemOrchestrator.get_health aggregation (via CatalogHealth)
```

**Forbidden reverse dependencies:**

- `instrument_loader` must not import `kite_websocket` runtime clients (type-only projection helpers are allowed when guarded).
- `instrument_loader` must not import strategy/risk/order/position modules.
- `instrument_loader` must not import `market_data_streaming` engines (may import `InstrumentDescriptor` type for projection).
- Downstream engines must not parse instrument CSV themselves once this module is wired.

---

## 7. Configuration — ApplicationConfiguration Projection

### 7.1 `InstrumentLoaderConfig`

Frozen dataclass. Validated in `__post_init__`.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled_underlyings` | `tuple[str, ...]` | required | Canonical index underlyings to retain. |
| `enabled_exchanges` | `tuple[str, ...]` | `("NSE", "NFO", "BSE", "BFO")` | Exchange allowlist. |
| `allow_experimental_underlyings` | `bool` | `False` | Permit non-catalog underlyings. |
| `allow_equity_fo` | `bool` | `False` | Permit NSE F&O stock underlyings. |
| `enabled_equity_underlyings` | `tuple[str, ...]` | `()` | Explicit equity F&O allowlist when enabled. |
| `include_index_spot` | `bool` | `True` | Retain INDEX/spot rows for enabled underlyings. |
| `include_futures` | `bool` | `True` | Retain futures rows. |
| `include_options` | `bool` | `True` | Retain CE/PE rows. |
| `include_volatility_index` | `bool` | `True` | Retain VIX-like rows when present/mapped. |
| `drop_expired` | `bool` | `True` | Filter contracts with expiry < as-of date. |
| `expiry_timezone` | `str` | `"Asia/Kolkata"` | Calendar timezone for expiry comparisons. |
| `duplicate_policy` | `str` | `"KEEP_FIRST_STABLE"` | See §12. |
| `require_non_empty_catalog` | `bool` | `True` | Fail load if zero records retained. |
| `max_records` | `int` | `500_000` | Hard safety cap against pathological files. |
| `default_strike_step` | `float` | `50.0` | Fallback strike step for ATM helpers. |
| `strike_step` | `Mapping[str, float]` | `{}` | Per-underlying strike step overrides. |
| `spot_overrides` | `Mapping[str, Mapping[str, Any]]` | `{}` | Optional injected spot metadata (token/symbol/exchange) from config projection — not hardcoded in module source. |
| `volatility_index_map` | `Mapping[str, str]` | `{}` | Optional map underlying → VIX tradingsymbol/name for association. |
| `cache_enabled` | `bool` | `True` | Persist/load local sealed cache. |
| `cache_directory` | `str \| None` | `None` | Directory for cache files; required if cache enabled in non-test profiles. |
| `cache_filename` | `str` | `"instrument_catalog_cache.json"` | Cache file name. |
| `prefer_cache_before_download` | `bool` | `True` | Try cache before broker download when fresh. |
| `cache_max_age_seconds` | `float` | `86400.0` | Maximum cache freshness. |
| `publish_events` | `bool` | `False` | Publish Event Bus topics on reload. |
| `environment_profile` | `EnvironmentProfile` | `DEVELOPMENT` | Audit/profile tag. |
| `runner_kind` | `str` | `"unknown"` | Audit tag (`cli`, `paper`, `live`, `test`). |
| `metadata` | `Mapping[str, str]` | `{}` | Non-secret free-form tags. |

### 7.2 Configuration validation rules

| Rule ID | Condition | Error code |
|---|---|---|
| CFG-IL-001 | `enabled_underlyings` non-empty | `IL.CONFIG.UNDERLYING_REQUIRED` |
| CFG-IL-002 | No duplicate underlyings after normalize | `IL.CONFIG.UNDERLYING_DUPLICATE` |
| CFG-IL-003 | Each underlying in supported catalogs unless experimental/equity flags allow | `IL.CONFIG.UNDERLYING_UNSUPPORTED` |
| CFG-IL-004 | `enabled_exchanges` non-empty; values in `{NSE,NFO,BSE,BFO,MCX}` (extensible) | `IL.CONFIG.EXCHANGE_INVALID` |
| CFG-IL-005 | `default_strike_step > 0` and finite | `IL.CONFIG.THRESHOLD_OUT_OF_RANGE` |
| CFG-IL-006 | Each `strike_step` value `> 0` | `IL.CONFIG.THRESHOLD_OUT_OF_RANGE` |
| CFG-IL-007 | `max_records >= 1` | `IL.CONFIG.THRESHOLD_OUT_OF_RANGE` |
| CFG-IL-008 | `cache_max_age_seconds >= 0` | `IL.CONFIG.THRESHOLD_OUT_OF_RANGE` |
| CFG-IL-009 | `duplicate_policy` in supported set | `IL.CONFIG.POLICY_INVALID` |
| CFG-IL-010 | If `cache_enabled` and profile is PAPER/PRODUCTION, `cache_directory` required | `IL.CONFIG.CACHE_PATH_REQUIRED` |
| CFG-IL-011 | If `allow_equity_fo` and `enabled_equity_underlyings` empty → warning issue only (not hard fail) | health warning |

### 7.3 Projection path

```text
ApplicationConfiguration
  → to_instrument_loader_config()   # owned by application_configuration / integration wiring
  → InstrumentLoader(config, master_client=..., clock=..., id_factory=...)
```

**Rule CFG-IL-012:** This module never reads `os.environ` or opens the application YAML itself.

### 7.4 Profile defaults (Appendix J summary)

| Profile | Notable defaults |
|---|---|
| DEVELOPMENT | `prefer_cache_before_download=True`, `require_non_empty_catalog=False` for empty fixtures, `allow_experimental_underlyings=True` |
| PAPER | Cache required, experimental off, equity FO off |
| PRODUCTION | Cache required, experimental off, equity FO off, stricter empty-catalog failure |

### 7.5 Helper

```python
def default_instrument_loader_config(
    profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT,
    *,
    enabled_underlyings: Sequence[str] = ("NIFTY",),
) -> InstrumentLoaderConfig: ...
```

---

## 8. Public API

### 8.1 Module constants

| Constant | Type | Description |
|---|---|---|
| `INSTRUMENT_LOADER_VERSION` | `str` | Module semver, `"1.0.0"`. |
| `INSTRUMENT_LOADER_SCHEMA_VERSION` | `str` | Serialization schema, `"1.0.0"`. |
| `PRODUCER_NAME` | `str` | `"broker.instrument_loader"`. |
| `SUPPORTED_PRIMARY_UNDERLYINGS` | `frozenset[str]` | §4.1 |
| `SUPPORTED_SECONDARY_UNDERLYINGS` | `frozenset[str]` | §4.2 |
| `SUPPORTED_INDEX_UNDERLYINGS` | `frozenset[str]` | Union of primary/secondary |
| `SUPPORTED_EXCHANGES` | `frozenset[str]` | `{"NSE","NFO","BSE","BFO","MCX"}` |
| `SUPPORTED_OPTION_TYPES` | `frozenset[str]` | `{"CE","PE"}` |
| `TOPIC_CATALOG_LOADED` | `str` | `"market.instruments.catalog.loaded"` |
| `TOPIC_CATALOG_FAILED` | `str` | `"market.instruments.catalog.failed"` |

### 8.2 Enumerations

#### 8.2.1 `InstrumentRole`

| Value | Meaning |
|---|---|
| `SPOT` | Index/spot underlying instrument |
| `FUTURE` | Futures contract |
| `OPTION_CE` | Call option |
| `OPTION_PE` | Put option |
| `VOLATILITY_INDEX` | Volatility index (e.g. INDIA VIX) |
| `EQUITY` | Cash equity (future use) |
| `UNKNOWN` | Unrecognized kind |

#### 8.2.2 `UnderlyingSupportTier`

`PRIMARY`, `SECONDARY`, `EQUITY_FO`, `EXPERIMENTAL`

#### 8.2.3 `CatalogLifecycleState`

`CREATED`, `LOADING`, `READY`, `RELOADING`, `DEGRADED`, `CLOSED`

#### 8.2.4 `CatalogHealthStatus`

`HEALTHY`, `DEGRADED`, `UNHEALTHY`, `UNKNOWN`

#### 8.2.5 `LookupStatus`

`HIT`, `MISS`, `AMBIGUOUS`, `REJECTED`

#### 8.2.6 `InstrumentSourceKind`

`BROKER_DOWNLOAD`, `LOCAL_CSV`, `LOCAL_JSON`, `IN_MEMORY_ROWS`, `CACHE`

#### 8.2.7 `DuplicatePolicy`

`KEEP_FIRST_STABLE`, `KEEP_LAST_STABLE`, `REJECT`

### 8.3 Exceptions

| Exception | Code prefix | When |
|---|---|---|
| `InstrumentLoaderConfigurationError` | `IL.CONFIG.*` | Invalid config |
| `InstrumentLoaderStateError` | `IL.STATE.*` | Illegal lifecycle transition / closed loader |
| `InstrumentParseError` | `IL.PARSE.*` | CSV/JSON parse failures |
| `InstrumentValidationError` | `IL.VALIDATION.*` | Row validation failures (may also be soft-discard) |
| `InstrumentLoaderIOError` | `IL.IO.*` | Filesystem / broker client IO failures |
| `InstrumentLoaderSerializationError` | `IL.SERIALIZATION.*` | JSON schema/payload errors |
| `InstrumentLookupError` | `IL.LOOKUP.*` | Strict lookup mode failures (optional raise path) |

All exceptions expose at least: `message: str`, `code: str`, and optional `field`, `underlying`, `instrument_token`, `tradingsymbol`.

### 8.4 Output models

#### 8.4.1 `InstrumentRecord` (frozen)

Canonical validated instrument row.

| Field | Type | Required | Description |
|---|---|---|---|
| `instrument_token` | `int` | Yes | Broker instrument token (`> 0`). |
| `exchange_token` | `int \| None` | No | Broker exchange token when present. |
| `tradingsymbol` | `str` | Yes | Broker trading symbol. |
| `name` | `str` | Yes | Broker `name` field (often underlying family). |
| `underlying` | `str` | Yes | Canonical underlying (`NIFTY`, …). |
| `exchange` | `str` | Yes | `NSE` / `NFO` / `BSE` / `BFO` / … |
| `instrument_type` | `str` | Yes | Broker type tag (`EQ`, `CE`, `PE`, `FUT`, `INDEX`, …). |
| `instrument_role` | `InstrumentRole` | Yes | Resolved platform role. |
| `segment` | `str \| None` | No | Broker segment. |
| `expiry` | `str \| None` | Cond. | `YYYY-MM-DD` for FUT/CE/PE; `None` for spot/index. |
| `strike` | `float \| None` | Cond. | Strike for options; `None` otherwise. |
| `option_type` | `str \| None` | Cond. | `CE` / `PE` for options. |
| `lot_size` | `int` | Yes | Lot size (`>= 1` for derivatives; may be `1` for index/EQ). |
| `tick_size` | `float` | Yes | Minimum price increment (`> 0`). |
| `quote_key` | `str` | Yes | Canonical `"EXCHANGE:SYMBOL"` key. |
| `support_tier` | `UnderlyingSupportTier` | Yes | Classification. |
| `is_expired` | `bool` | Yes | Whether expiry < as-of at seal time (normally `False` in retained set). |
| `raw_name` | `str \| None` | No | Original broker name before normalization. |
| `metadata` | `Mapping[str, str]` | No | Non-secret tags. |

**Rule MODEL-IL-001:** `quote_key` is always `f"{exchange}:{tradingsymbol}"` after normalization unless an explicit override is supplied in metadata under key `quote_key_override` (rare; tests only).

#### 8.4.2 `InstrumentCatalog` (frozen)

| Field | Type | Description |
|---|---|---|
| `catalog_id` | `str` | Stable ID for this sealed instance. |
| `schema_version` | `str` | `INSTRUMENT_LOADER_SCHEMA_VERSION`. |
| `loaded_at` | `datetime` | Timezone-aware UTC seal timestamp. |
| `as_of_date` | `str` | `YYYY-MM-DD` calendar date used for expiry filtering. |
| `source_kind` | `InstrumentSourceKind` | Provenance. |
| `source_uri` | `str \| None` | File path or logical broker URI. |
| `enabled_underlyings` | `tuple[str, ...]` | Config snapshot. |
| `enabled_exchanges` | `tuple[str, ...]` | Config snapshot. |
| `records` | `tuple[InstrumentRecord, ...]` | Deterministically ordered records. |
| `record_count` | `int` | `len(records)`. |
| `indexes` | `CatalogIndexes` | Frozen index bundle (may be omitted from JSON dumps when `include_indexes=False`). |
| `statistics` | `CatalogStatistics` | Seal-time statistics. |
| `metadata` | `Mapping[str, str]` | Non-secret tags. |

Ordering rule for `records`: sort by `(underlying, exchange, expiry or "", strike or -1, option_type or "", tradingsymbol, instrument_token)`.

#### 8.4.3 `CatalogIndexes` (frozen)

| Index | Key | Value |
|---|---|---|
| `by_token` | `int` | `InstrumentRecord` |
| `by_quote_key` | `str` | `InstrumentRecord` |
| `by_tradingsymbol` | `(exchange, tradingsymbol)` | `InstrumentRecord` |
| `by_underlying` | `str` | `tuple[InstrumentRecord, ...]` |
| `by_underlying_expiry` | `(underlying, expiry)` | `tuple[InstrumentRecord, ...]` |
| `by_underlying_expiry_strike` | `(underlying, expiry, strike)` | `tuple[InstrumentRecord, ...]` |
| `by_underlying_role` | `(underlying, InstrumentRole)` | `tuple[InstrumentRecord, ...]` |
| `option_expiries` | `underlying` | `tuple[str, ...]` ascending |
| `future_expiries` | `underlying` | `tuple[str, ...]` ascending |
| `strikes` | `(underlying, expiry)` | `tuple[float, ...]` ascending unique |

Indexes are constructed once at seal time. Public accessors never expose mutable dicts.

#### 8.4.4 `LookupResult` (frozen)

| Field | Type | Description |
|---|---|---|
| `status` | `LookupStatus` | Hit/miss/ambiguous/rejected. |
| `query_name` | `str` | Logical query identifier. |
| `records` | `tuple[InstrumentRecord, ...]` | Matching records (empty on miss). |
| `primary` | `InstrumentRecord \| None` | Convenience first/only record. |
| `reason_code` | `str \| None` | Stable code on miss/reject/ambiguous. |
| `reason_message` | `str \| None` | Human-readable detail. |
| `diagnostics` | `Mapping[str, Any]` | Optional structured diagnostics. |

#### 8.4.5 `CatalogHealth` (frozen)

| Field | Type | Description |
|---|---|---|
| `report_id` | `str` | Report ID. |
| `as_of` | `datetime` | Report timestamp. |
| `lifecycle_state` | `CatalogLifecycleState` | Current lifecycle. |
| `overall_health` | `CatalogHealthStatus` | Aggregated status. |
| `has_catalog` | `bool` | Whether a sealed catalog is available. |
| `catalog_id` | `str \| None` | Current catalog ID. |
| `record_count` | `int` | Current retained records. |
| `enabled_underlyings` | `tuple[str, ...]` | Configured underlyings. |
| `underlyings_with_records` | `tuple[str, ...]` | Underlyings present in catalog. |
| `underlyings_missing_records` | `tuple[str, ...]` | Enabled but absent. |
| `seconds_since_load` | `float \| None` | Age of catalog. |
| `issues` | `tuple[CatalogHealthIssue, ...]` | Structured issues. |
| `statistics` | `CatalogStatistics` | Echo of latest stats. |
| `metadata` | `Mapping[str, str]` | Non-secret tags. |

#### 8.4.6 `CatalogHealthIssue` (frozen)

| Field | Type |
|---|---|
| `issue_code` | `str` |
| `severity` | `Literal["info","warning","error"]` |
| `message` | `str` |
| `underlying` | `str \| None` |
| `instrument_token` | `int \| None` |

#### 8.4.7 `CatalogStatistics` (frozen)

| Field | Type | Description |
|---|---|---|
| `as_of` | `datetime` | Stats timestamp. |
| `source_kind` | `InstrumentSourceKind \| None` | Last load source. |
| `load_duration_ms` | `float` | End-to-end load duration. |
| `parse_duration_ms` | `float` | Parse phase duration. |
| `validate_duration_ms` | `float` | Validate/normalize/dedupe/filter duration. |
| `index_duration_ms` | `float` | Index build duration. |
| `raw_row_count` | `int` | Rows seen before filtering. |
| `retained_record_count` | `int` | Final catalog size. |
| `discarded_invalid_count` | `int` | Failed validation. |
| `discarded_duplicate_count` | `int` | Collapsed duplicates. |
| `discarded_expired_count` | `int` | Removed by expiry filter. |
| `discarded_underlying_count` | `int` | Outside enabled underlyings. |
| `discarded_exchange_count` | `int` | Outside enabled exchanges. |
| `discarded_equity_fo_count` | `int` | Equity FO discarded by policy. |
| `option_count` | `int` | Retained CE+PE. |
| `future_count` | `int` | Retained futures. |
| `spot_count` | `int` | Retained spot/index. |
| `volatility_count` | `int` | Retained vol index rows. |
| `expiry_count` | `int` | Distinct option expiries retained. |
| `underlying_counts` | `Mapping[str, int]` | Per-underlying retained counts. |
| `last_error_code` | `str \| None` | Last failed load code, if any. |

### 8.5 `InstrumentMasterClient` protocol

```python
class InstrumentMasterClient(Protocol):
    def fetch_instrument_rows(
        self,
        *,
        exchange: str,
    ) -> Sequence[Mapping[str, Any]]:
        """Return raw broker instrument rows for one exchange."""
```

**Rule API-IL-001:** The loader never constructs `KiteConnect` itself.

### 8.6 `InstrumentLoader` facade

```python
class InstrumentLoader:
    def __init__(
        self,
        config: InstrumentLoaderConfig,
        *,
        master_client: InstrumentMasterClient | None = None,
        event_bus: EventBus | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None: ...

    # lifecycle
    def get_status(self) -> CatalogLifecycleState: ...
    def close(self) -> None: ...

    # loaders
    def load_from_broker(self, *, exchanges: Sequence[str] | None = None) -> InstrumentCatalog: ...
    def load_from_file(self, path: str | Path, *, source_kind: InstrumentSourceKind | None = None) -> InstrumentCatalog: ...
    def load_from_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        source_kind: InstrumentSourceKind = InstrumentSourceKind.IN_MEMORY_ROWS,
        source_uri: str | None = None,
    ) -> InstrumentCatalog: ...
    def load_from_cache(self) -> InstrumentCatalog: ...
    def reload(self) -> InstrumentCatalog: ...  # uses last source descriptor

    # access
    def get_catalog(self) -> InstrumentCatalog: ...
    def get_health(self) -> CatalogHealth: ...
    def get_statistics(self) -> CatalogStatistics: ...

    # lookups
    def get_by_token(self, instrument_token: int) -> LookupResult: ...
    def get_by_tradingsymbol(self, exchange: str, tradingsymbol: str) -> LookupResult: ...
    def get_by_quote_key(self, quote_key: str) -> LookupResult: ...
    def get_by_underlying(self, underlying: str) -> LookupResult: ...
    def get_by_underlying_and_expiry(self, underlying: str, expiry: str) -> LookupResult: ...
    def get_options(
        self,
        underlying: str,
        *,
        expiry: str | None = None,
        strike: float | None = None,
        option_type: str | None = None,
    ) -> LookupResult: ...
    def get_futures(self, underlying: str, *, expiry: str | None = None) -> LookupResult: ...
    def get_spot(self, underlying: str) -> LookupResult: ...
    def get_lot_size(self, underlying: str, *, expiry: str | None = None) -> int: ...

    # queries
    def find_nearest_expiry(self, underlying: str, *, as_of: date | None = None, kind: str = "option") -> LookupResult: ...
    def find_weekly_expiries(self, underlying: str, *, as_of: date | None = None, limit: int | None = None) -> LookupResult: ...
    def find_monthly_expiries(self, underlying: str, *, as_of: date | None = None, limit: int | None = None) -> LookupResult: ...
    def find_closest_expiry(self, underlying: str, *, target: date, kind: str = "option") -> LookupResult: ...
    def resolve_atm_strike(self, underlying: str, *, spot: float, expiry: str, strike_step: float | None = None) -> float: ...
    def find_nearest_strike(self, underlying: str, *, expiry: str, target_price: float) -> LookupResult: ...
    def query_atm_options(self, underlying: str, *, spot: float, expiry: str) -> LookupResult: ...
    def query_itm_options(self, underlying: str, *, spot: float, expiry: str, option_type: str, depth: int = 1) -> LookupResult: ...
    def query_otm_options(self, underlying: str, *, spot: float, expiry: str, option_type: str, depth: int = 1) -> LookupResult: ...

    # projections
    def project_descriptors(
        self,
        underlying: str,
        *,
        expiry: str | None = None,
        spot: float | None = None,
        strikes_each_side: int = 5,
        include_futures: bool | None = None,
        include_spot: bool | None = None,
        include_volatility_index: bool | None = None,
    ) -> tuple[Any, ...]: ...
    def project_subscriptions(
        self,
        underlying: str,
        *,
        expiry: str | None = None,
        spot: float | None = None,
        strikes_each_side: int = 5,
        mode: str | None = None,
    ) -> tuple[Any, ...]: ...

    # persistence helpers
    def save_cache(self, path: str | Path | None = None) -> Path: ...
```

**Rule API-IL-002:** All public methods that return records wrap them in `LookupResult` except projection helpers (which return consumer DTO tuples) and `resolve_atm_strike` / `get_lot_size` (scalar helpers). Scalar helpers raise typed errors in strict failure cases.

**Rule API-IL-003:** `get_catalog()` raises `IL.STATE.NOT_READY` when no catalog is sealed.

---

## 9. Instrument Master Sources

### 9.1 Broker download

`load_from_broker(exchanges=None)`:

1. Require `master_client is not None` else raise `IL.STATE.CLIENT_NOT_CONFIGURED`.
2. Resolve exchange list: argument or `config.enabled_exchanges`.
3. For each exchange (deterministic sorted order), call `master_client.fetch_instrument_rows(exchange=...)`.
4. Tag each row with `_source_exchange` if missing.
5. Concatenate rows preserving per-exchange order, then global stable sort by token for duplicate detection input.
6. Run the shared pipeline (§10–§14).
7. Seal catalog with `source_kind=BROKER_DOWNLOAD`, `source_uri="broker://instruments"`.
8. Optionally write cache when `cache_enabled=True`.

**Rule SRC-IL-001:** Download failures raise `InstrumentLoaderIOError` with code `IL.IO.BROKER_FETCH_FAILED` and must not partially swap a half-built catalog over a previously healthy catalog unless `config.replace_on_failure=True` (default `False` — keep previous READY catalog and mark DEGRADED).

### 9.2 Local CSV file

`load_from_file(path)`:

1. Resolve path; missing file → `IL.IO.FILE_NOT_FOUND`.
2. Detect format by suffix (`.csv` required path; `.json` optional).
3. Parse via `InstrumentCsvParser` / JSON loader.
4. Run shared pipeline.
5. Seal with `source_kind=LOCAL_CSV` or `LOCAL_JSON`, `source_uri=str(path)`.

### 9.3 In-memory rows

`load_from_rows(rows)` accepts mappings already shaped like broker instrument dicts. Used by unit tests and Integration Engine replay.

### 9.4 Cache load

`load_from_cache()`:

1. Require cache configuration.
2. Read versioned JSON catalog cache.
3. Reject unsupported schema major (`IL.SERIALIZATION.UNSUPPORTED_VERSION`).
4. Reject stale cache when `(now - loaded_at) > cache_max_age_seconds` (`IL.IO.CACHE_STALE`) unless `allow_stale_cache=True` (Development only).
5. Rebuild runtime indexes from records (indexes may be omitted in cache payload).

### 9.5 Reload semantics

`reload()` reuses the last successful `SourceDescriptor(source_kind, source_uri, exchanges)`.

| Previous source | Reload behaviour |
|---|---|
| BROKER_DOWNLOAD | Re-fetch exchanges |
| LOCAL_CSV / LOCAL_JSON | Re-read same path |
| IN_MEMORY_ROWS | Raise `IL.STATE.RELOAD_UNSUPPORTED` (caller must supply rows again) |
| CACHE | Re-read cache file |

---

## 10. CSV Parsing

### 10.1 Expected Zerodha-style columns

| CSV column | Internal field |
|---|---|
| `instrument_token` | `instrument_token` |
| `exchange_token` | `exchange_token` |
| `tradingsymbol` | `tradingsymbol` |
| `name` | `name` / underlying candidate |
| `last_price` | ignored for catalog |
| `expiry` | `expiry` |
| `strike` | `strike` |
| `tick_size` | `tick_size` |
| `lot_size` | `lot_size` |
| `instrument_type` | `instrument_type` |
| `segment` | `segment` |
| `exchange` | `exchange` |

### 10.2 Parser rules

| Rule ID | Statement |
|---|---|
| PARSE-IL-001 | UTF-8 encoding; accept optional BOM. |
| PARSE-IL-002 | Dialect = comma-separated, header required. |
| PARSE-IL-003 | Missing required columns → `IL.PARSE.MISSING_COLUMNS`. |
| PARSE-IL-004 | Empty file / header-only → zero rows (may later fail `require_non_empty_catalog`). |
| PARSE-IL-005 | Per-row parse errors soft-discard with reason `IL.PARSE.ROW_MALFORMED` unless `strict_parse=True`. |
| PARSE-IL-006 | JSON files must be a list of objects or `{"records":[...]}`. |

### 10.3 Pseudocode

```python
def parse_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_columns(reader.fieldnames)
        rows = []
        for index, raw in enumerate(reader):
            try:
                rows.append(normalize_raw_row(raw, row_index=index))
            except InstrumentParseError:
                if strict:
                    raise
                discarded.append(...)
        return rows
```

---

## 11. Normalization

### 11.1 Field normalization table

| Field | Normalization |
|---|---|
| `exchange` | strip + uppercase |
| `tradingsymbol` | strip (preserve broker casing except surrounding whitespace) |
| `name` | strip + uppercase → underlying candidate |
| `instrument_type` | strip + uppercase |
| `expiry` | parse to `YYYY-MM-DD` (accept `YYYY-MM-DD`, `DD-MMM-YYYY`, epoch-ms broker variants documented in adapter tests) |
| `strike` | `float`; blank → `None` |
| `option_type` | derived from `instrument_type` when `CE`/`PE` |
| `lot_size` | `int` |
| `tick_size` | `float` |
| `instrument_token` | `int` |

### 11.2 Underlying resolution algorithm

1. If `instrument_type in {CE,PE,FUT}`: underlying = normalized `name`.
2. If `instrument_type == INDEX`: map known index names through `INDEX_NAME_ALIASES` (config-injected + module defaults for catalog names only — **aliases map names, never tokens**).
3. If row matches `volatility_index_map` values: role = `VOLATILITY_INDEX`; underlying association from map key.
4. If `allow_equity_fo` and name in `enabled_equity_underlyings`: underlying = name.
5. Else mark underlying unresolved → discard with `IL.VALIDATION.UNDERLYING_UNRESOLVED` (or experimental accept).

Default alias examples (names only):

```python
INDEX_NAME_ALIASES = {
    "NIFTY 50": "NIFTY",
    "NIFTY50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "SENSEX": "SENSEX",
    "NIFTY FIN SERVICE": "FINNIFTY",
    "NIFTY MID SELECT": "MIDCPNIFTY",
}
```

**Rule NORM-IL-001:** Alias tables must not contain `instrument_token` values.

### 11.3 Role resolution

```python
def resolve_instrument_role(instrument_type: str, *, name: str | None = None) -> InstrumentRole:
    t = instrument_type.upper()
    if t in {"INDEX"}: return InstrumentRole.SPOT
    if t == "FUT": return InstrumentRole.FUTURE
    if t == "CE": return InstrumentRole.OPTION_CE
    if t == "PE": return InstrumentRole.OPTION_PE
    if t in {"EQ"}: return InstrumentRole.EQUITY
    # VIX detection by name/tradingsymbol heuristics + config map
    return InstrumentRole.UNKNOWN
```

---

## 12. Validation

### 12.1 Hard validation matrix (VAL-IL-*)

| Rule ID | Condition | Code | Default disposition |
|---|---|---|---|
| VAL-IL-001 | `instrument_token` is int and `> 0` | `IL.VALIDATION.INVALID_TOKEN` | discard |
| VAL-IL-002 | `exchange` in `enabled_exchanges` ∩ `SUPPORTED_EXCHANGES` | `IL.VALIDATION.INVALID_EXCHANGE` | discard |
| VAL-IL-003 | `tradingsymbol` non-empty | `IL.VALIDATION.MISSING_SYMBOL` | discard |
| VAL-IL-004 | Options require `expiry` matching `YYYY-MM-DD` | `IL.VALIDATION.MISSING_EXPIRY` | discard |
| VAL-IL-005 | Options require finite `strike > 0` | `IL.VALIDATION.INVALID_STRIKE` | discard |
| VAL-IL-006 | Options require `option_type in {CE,PE}` | `IL.VALIDATION.INVALID_OPTION_TYPE` | discard |
| VAL-IL-007 | Futures require `expiry` | `IL.VALIDATION.MISSING_EXPIRY` | discard |
| VAL-IL-008 | `lot_size >= 1` | `IL.VALIDATION.INVALID_LOT_SIZE` | discard |
| VAL-IL-009 | `tick_size > 0` finite | `IL.VALIDATION.INVALID_TICK_SIZE` | discard |
| VAL-IL-010 | Underlying enabled under config policy | `IL.VALIDATION.UNDERLYING_NOT_ENABLED` | discard |
| VAL-IL-011 | Role not `UNKNOWN` unless `allow_unknown_roles` | `IL.VALIDATION.UNKNOWN_ROLE` | discard |
| VAL-IL-012 | Spot/index must not carry option strike | `IL.VALIDATION.INCONSISTENT_FIELDS` | discard |

### 12.2 Strict mode

When `strict_validation=True`, the first hard failure aborts the entire load with `InstrumentValidationError` instead of soft-discard. Default is soft-discard with statistics.

### 12.3 Duplicate detection (DUP-IL-*)

Duplicates are detected after validation, before expiry filtering.

| Key | Code |
|---|---|
| duplicate `instrument_token` | `IL.VALIDATION.DUPLICATE_TOKEN` |
| duplicate `(exchange, tradingsymbol)` | `IL.VALIDATION.DUPLICATE_SYMBOL` |

Policy:

| Policy | Behaviour |
|---|---|
| `KEEP_FIRST_STABLE` | Keep earliest row in stable sorted input order |
| `KEEP_LAST_STABLE` | Keep latest row in stable sorted input order |
| `REJECT` | Abort load with `IL.VALIDATION.DUPLICATE_TOKEN` / `DUPLICATE_SYMBOL` |

**Rule DUP-IL-001:** Discarded duplicates increment `discarded_duplicate_count` and are listed in optional diagnostics (bounded to first N examples).

---

## 13. Expiry Filtering

| Rule ID | Statement |
|---|---|
| EXP-IL-001 | `as_of_date` = `clock().astimezone(ZoneInfo(expiry_timezone)).date().isoformat()` unless caller overrides. |
| EXP-IL-002 | If `drop_expired=True`, discard CE/PE/FUT with `expiry < as_of_date`. |
| EXP-IL-003 | Spot/index/EQ/VIX rows are never expiry-discarded. |
| EXP-IL-004 | `expiry == as_of_date` is retained (expiry-day contracts are valid). |
| EXP-IL-005 | Discarded rows increment `discarded_expired_count`. |

---

## 14. Catalog Construction and Indexes

### 14.1 Seal algorithm

```text
rows
 → normalize
 → validate (collect keeps + discards)
 → sort stable by token then symbol
 → resolve duplicates
 → filter expired
 → enforce max_records
 → build InstrumentRecord tuple (sorted canonical order)
 → build CatalogIndexes
 → build CatalogStatistics
 → freeze InstrumentCatalog
 → atomic swap current_catalog reference
 → transition lifecycle READY
 → optional cache write
 → optional EventBus publish
```

### 14.2 Index build rules

| Rule ID | Statement |
|---|---|
| IDX-IL-001 | Every retained record appears in `by_token` exactly once. |
| IDX-IL-002 | `by_underlying[u]` is sorted by the catalog record order subset. |
| IDX-IL-003 | `option_expiries[u]` contains unique option expiries ascending. |
| IDX-IL-004 | `strikes[(u,e)]` contains unique strikes ascending. |
| IDX-IL-005 | Index structures exposed to callers are immutable (`MappingProxyType` / tuples). |

### 14.3 Memory bounds

| Structure | Bound |
|---|---|
| records | `<= max_records` |
| discard diagnostics examples | `<= 100` per reason |
| cached previous catalog | at most 1 |

---

## 15. Lookup APIs

All lookups require lifecycle `READY` or `DEGRADED` with an available catalog.

### 15.1 By instrument token

```python
def get_by_token(self, instrument_token: int) -> LookupResult: ...
```

- HIT with `primary` set when present.
- MISS with `IL.LOOKUP.TOKEN_NOT_FOUND` otherwise.
- REJECTED when `instrument_token <= 0` (`IL.LOOKUP.INVALID_TOKEN`).

### 15.2 By trading symbol

```python
def get_by_tradingsymbol(self, exchange: str, tradingsymbol: str) -> LookupResult: ...
```

Normalizes exchange; MISS → `IL.LOOKUP.SYMBOL_NOT_FOUND`.

### 15.3 By quote key

Accepts `"NFO:NIFTY25AUG24500CE"`; splits on first `:`.

### 15.4 By underlying

Returns all retained records for the underlying (`HIT` even if empty set? **No** — empty → `MISS` / `IL.LOOKUP.UNDERLYING_EMPTY`).

### 15.5 By underlying + expiry

Filters options+futures for that expiry.

### 15.6 Options / futures / spot dedicated getters

`get_options`, `get_futures`, `get_spot` apply role filters and optional strike/type predicates.

### 15.7 Lot size

```python
def get_lot_size(self, underlying: str, *, expiry: str | None = None) -> int: ...
```

Deterministic rule: use lot size from nearest option record for expiry (or nearest expiry if omitted). If conflicting lot sizes exist for the same underlying/expiry, raise `IL.LOOKUP.AMBIGUOUS_LOT_SIZE` in strict mode; otherwise return the minimum lot size and emit health warning.

---

## 16. Query APIs

### 16.1 Nearest expiry

`find_nearest_expiry(underlying, as_of=None, kind="option")`

- Build ascending expiry list from indexes (`option_expiries` or `future_expiries`).
- Return first expiry `>= as_of`.
- If none, return last expiry (past-only fallback) with diagnostic `IL.LOOKUP.PAST_ONLY_EXPIRY`, or MISS when list empty.

### 16.2 Weekly expiries

`find_weekly_expiries(underlying, as_of=None, limit=None)`

Heuristic (documented, deterministic):

1. Collect future option expiries `>= as_of`.
2. Classify an expiry as **monthly** if it is the last expiry falling in that calendar month among the underlying's expiries; all others are **weekly**.
3. Return weekly expiries ascending.

**Rule QRY-IL-001:** This classification is calendar/index-expiry heuristic for Indian index options; it is not a broker API flag.

### 16.3 Monthly expiries

Complement of weekly classification (§16.2), ascending.

### 16.4 Closest expiry

`find_closest_expiry(underlying, target, kind="option")` minimizes `abs(expiry_date - target)`; ties break to the later expiry.

### 16.5 ATM strike

```python
def resolve_atm_strike(underlying, *, spot, expiry, strike_step=None) -> float:
    step = strike_step or config.strike_step.get(underlying, config.default_strike_step)
    strikes = catalog.indexes.strikes[(underlying, expiry)]
    snapped = round(spot / step) * step
    if not strikes:
        return snapped
    return min(strikes, key=lambda s: (abs(s - snapped), s))
```

Pure function of catalog strikes + spot + step (mirrors streaming `derive_atm` semantics).

### 16.6 Nearest strike

Returns the `InstrumentRecord` pair/list at the strike nearest to `target_price` for the expiry (both CE/PE when present).

### 16.7 ATM / ITM / OTM option queries

| Query | Definition |
|---|---|
| ATM | CE+PE at `resolve_atm_strike(...)` |
| ITM calls | strikes `< spot` (depth N nearest) |
| ITM puts | strikes `> spot` (depth N nearest) |
| OTM calls | strikes `> spot` |
| OTM puts | strikes `< spot` |

`depth` selects how many strikes away from ATM (or from spot boundary) to include.

**Rule QRY-IL-002:** ITM/OTM helpers never require live quotes beyond the caller-supplied `spot` argument; the catalog remains static metadata.

---

## 17. Consumer Projection

### 17.1 Streaming descriptors

`project_descriptors(...)` returns objects compatible with `broker.market_data_streaming.InstrumentDescriptor`:

Required projection fields:

- `instrument_token`, `underlying`, `quote_key`, `exchange`, `tradingsymbol`
- `instrument_kind`, `instrument_role`
- `strike`, `option_type`, `expiry`, `lot_size`, `tick_size`
- `support_tier`

Selection algorithm:

1. Resolve expiry (nearest if `None`).
2. Resolve ATM from `spot` when provided; else use median strike.
3. Select strikes window: ATM ± `strikes_each_side` using strike step grid present in catalog.
4. Include CE/PE pairs for selected strikes.
5. Optionally include spot, nearest future, mapped volatility index.

**Rule PROJ-IL-001:** Projection never invents tokens. Missing spot/future/VIX simply omits those descriptors.

### 17.2 WebSocket subscriptions

`project_subscriptions(...)` returns objects compatible with `broker.kite_websocket.SubscriptionInstrument`:

- `instrument_token`, `underlying`, `tradingsymbol`, `exchange`
- `instrument_kind`
- optional `mode`

### 17.3 Fulfills market_data_streaming Appendix K

This module is the production owner of the external resolver contract illustrated in `docs/specifications/market_data_streaming.md` Appendix K.

---

## 18. Caching

### 18.1 Cache file format

Versioned JSON document:

```json
{
  "schema_version": "1.0.0",
  "catalog": { "...InstrumentCatalog without indexes..." },
  "checksum": "sha256:...",
  "written_at": "2026-08-05T00:30:00Z"
}
```

### 18.2 Rules

| Rule ID | Statement |
|---|---|
| CACHE-IL-001 | Cache write is atomic (temp file + replace). |
| CACHE-IL-002 | Checksum covers canonical record payload. |
| CACHE-IL-003 | Cache must not store secrets. |
| CACHE-IL-004 | Corrupt cache → `IL.IO.CACHE_CORRUPT`; loader falls back to source when available. |

---

## 19. Performance

### 19.1 Budgets (reference hardware: developer laptop / small VM)

| Operation | Budget |
|---|---|
| Parse 100k CSV rows | ≤ 1.5 s |
| Validate+dedupe+filter 100k rows | ≤ 1.0 s |
| Build indexes for 50k retained records | ≤ 500 ms |
| `get_by_token` | O(1), ≤ 5 µs typical |
| `get_options(underlying, expiry)` | O(k) in matches, ≤ 50 µs for k≤400 |
| `resolve_atm_strike` | ≤ 20 µs for ≤ 200 strikes |
| Concurrent readers (32 threads) | no errors; p99 lookup < 100 µs |

### 19.2 Hot-path rules

| Rule ID | Statement |
|---|---|
| PERF-IL-001 | Lookups never re-parse CSV. |
| PERF-IL-002 | Reload parse/index work happens off the read path; swap is atomic. |
| PERF-IL-003 | No network I/O inside lookup/query methods. |
| PERF-IL-004 | Projection may allocate new DTO tuples but must not rebuild indexes. |

### 19.3 Threading model

- `_lifecycle_lock` for state transitions.
- `_catalog_lock` (RW or atomic reference + immutable catalogs) for current catalog pointer.
- Readers copy the catalog reference once per call and operate lock-free on the immutable object.

---

## 20. Health Reporting

### 20.1 Status derivation

| Condition | Status |
|---|---|
| READY + all enabled underlyings have records + no error issues | HEALTHY |
| READY/DEGRADED + some underlyings missing records | DEGRADED |
| Last load failed and no catalog available | UNHEALTHY |
| CREATED / CLOSED / LOADING with no prior catalog | UNKNOWN |

### 20.2 Issue codes

| Code | Severity | Meaning |
|---|---|---|
| `IL.HEALTH.NO_CATALOG` | error | No sealed catalog |
| `IL.HEALTH.UNDERLYING_MISSING` | warning/error | Enabled underlying has zero records |
| `IL.HEALTH.CACHE_STALE` | warning | Serving cache beyond soft age |
| `IL.HEALTH.HIGH_DISCARD_RATIO` | warning | Discarded/raw > 0.5 |
| `IL.HEALTH.MISSING_SPOT` | warning | Options present but spot missing for underlying |
| `IL.HEALTH.LOAD_FAILED` | error | Last load failed |
| `IL.HEALTH.EMPTY_EXPIRIES` | warning | Underlying has options role expectation but no expiries |

### 20.3 `validate()` static checks

Non-mutating consistency checks similar in spirit to streaming `validate()`:

- enabled underlyings without records
- options without spot
- conflicting lot sizes
- cache path misconfiguration

---

## 21. Statistics

`get_statistics()` returns the frozen `CatalogStatistics` from the latest load attempt (success or failure). Counters are not mutated by lookups.

Reset policy: statistics represent the last load only (not cumulative across days), unless `cumulative_statistics=True` (default False).

---

## 22. Error Codes

### 22.1 Configuration

| Code | Meaning |
|---|---|
| `IL.CONFIG.UNDERLYING_REQUIRED` | Empty underlyings |
| `IL.CONFIG.UNDERLYING_DUPLICATE` | Duplicate underlying |
| `IL.CONFIG.UNDERLYING_UNSUPPORTED` | Not in allowlist |
| `IL.CONFIG.EXCHANGE_INVALID` | Bad exchange |
| `IL.CONFIG.THRESHOLD_OUT_OF_RANGE` | Numeric threshold invalid |
| `IL.CONFIG.POLICY_INVALID` | Bad duplicate policy |
| `IL.CONFIG.CACHE_PATH_REQUIRED` | Cache directory missing |

### 22.2 State

| Code | Meaning |
|---|---|
| `IL.STATE.NOT_READY` | Catalog not sealed |
| `IL.STATE.CLOSED` | Loader closed |
| `IL.STATE.INVALID_TRANSITION` | Illegal lifecycle move |
| `IL.STATE.CLIENT_NOT_CONFIGURED` | Broker client missing |
| `IL.STATE.RELOAD_UNSUPPORTED` | Cannot reload in-memory source |
| `IL.STATE.LOAD_IN_PROGRESS` | Concurrent load rejected |

### 22.3 Parse / IO

| Code | Meaning |
|---|---|
| `IL.PARSE.MISSING_COLUMNS` | CSV header incomplete |
| `IL.PARSE.ROW_MALFORMED` | Row parse failure |
| `IL.PARSE.JSON_INVALID` | JSON payload invalid |
| `IL.IO.FILE_NOT_FOUND` | Missing file |
| `IL.IO.BROKER_FETCH_FAILED` | Download failed |
| `IL.IO.CACHE_STALE` | Cache too old |
| `IL.IO.CACHE_CORRUPT` | Cache checksum/schema failure |
| `IL.IO.WRITE_FAILED` | Atomic cache write failed |

### 22.4 Validation / lookup / serialization

| Code | Meaning |
|---|---|
| `IL.VALIDATION.INVALID_TOKEN` | Token ≤ 0 / non-int |
| `IL.VALIDATION.INVALID_EXCHANGE` | Exchange rejected |
| `IL.VALIDATION.MISSING_SYMBOL` | Empty symbol |
| `IL.VALIDATION.MISSING_EXPIRY` | Derivative missing expiry |
| `IL.VALIDATION.INVALID_STRIKE` | Strike invalid |
| `IL.VALIDATION.INVALID_OPTION_TYPE` | Option type invalid |
| `IL.VALIDATION.INVALID_LOT_SIZE` | Lot size invalid |
| `IL.VALIDATION.INVALID_TICK_SIZE` | Tick size invalid |
| `IL.VALIDATION.UNDERLYING_NOT_ENABLED` | Underlying filtered |
| `IL.VALIDATION.UNDERLYING_UNRESOLVED` | Could not map underlying |
| `IL.VALIDATION.UNKNOWN_ROLE` | Role unresolved |
| `IL.VALIDATION.INCONSISTENT_FIELDS` | Contradictory fields |
| `IL.VALIDATION.DUPLICATE_TOKEN` | Duplicate token |
| `IL.VALIDATION.DUPLICATE_SYMBOL` | Duplicate symbol |
| `IL.LOOKUP.TOKEN_NOT_FOUND` | Miss by token |
| `IL.LOOKUP.SYMBOL_NOT_FOUND` | Miss by symbol |
| `IL.LOOKUP.UNDERLYING_EMPTY` | No rows for underlying |
| `IL.LOOKUP.EXPIRY_NOT_FOUND` | Expiry absent |
| `IL.LOOKUP.INVALID_TOKEN` | Bad token argument |
| `IL.LOOKUP.AMBIGUOUS_LOT_SIZE` | Conflicting lots |
| `IL.LOOKUP.PAST_ONLY_EXPIRY` | Diagnostic for fallback |
| `IL.SERIALIZATION.MALFORMED` | Bad JSON/payload |
| `IL.SERIALIZATION.UNSUPPORTED_VERSION` | Schema major mismatch |

---

## 23. Security

| Rule ID | Statement |
|---|---|
| SEC-IL-001 | Catalog files and caches must not contain access tokens or API secrets. |
| SEC-IL-002 | `metadata` maps are non-secret; tests assert no token-shaped fixtures. |
| SEC-IL-003 | Broker download uses injected authenticated client; this module never logs full session credentials. |
| SEC-IL-004 | Cache directory permissions are the operator's responsibility; module uses atomic writes only. |
| SEC-IL-005 | Path arguments are used as-is after expansion; no shell invocation. |

---

## 24. Thread Safety and Determinism

### 24.1 Concurrency rules

| Rule ID | Statement |
|---|---|
| THR-IL-001 | At most one load/reload executes at a time (`LOAD_IN_PROGRESS` on conflict). |
| THR-IL-002 | Readers may proceed during load; they observe the previous sealed catalog until atomic swap. |
| THR-IL-003 | After swap, new readers observe the new catalog; in-flight readers finish on the old immutable catalog. |
| THR-IL-004 | Health/statistics reads are consistent snapshots. |

### 24.2 Determinism

**Rule DET-IL-001:** Identical row sequences + identical config + identical clock + identical id_factory produce identical `InstrumentCatalog` payloads (including record order and index key sets).

**Rule DET-IL-002:** Query helpers are pure functions of the sealed catalog + explicit arguments.

---

## 25. Serialization

### 25.1 Versioned JSON

Public serializers:

- `serialize_instrument_record` / `deserialize_instrument_record`
- `serialize_instrument_catalog` / `deserialize_instrument_catalog`
- `serialize_catalog_health` / `deserialize_catalog_health`
- `serialize_catalog_statistics` / `deserialize_catalog_statistics`
- `serialize_lookup_result` / `deserialize_lookup_result`
- `*_to_json` / `*_from_json` convenience wrappers

### 25.2 Rules

| Rule ID | Statement |
|---|---|
| SER-IL-001 | Schema field `schema_version` present on top-level documents. |
| SER-IL-002 | Datetimes serialized as ISO-8601 UTC with `Z`. |
| SER-IL-003 | Enums serialized by value. |
| SER-IL-004 | Unknown major schema → `IL.SERIALIZATION.UNSUPPORTED_VERSION`. |
| SER-IL-005 | Catalog JSON may omit bulky indexes (`include_indexes=False` default for cache); deserialize rebuilds indexes. |
| SER-IL-006 | Deserialization re-validates record invariants before sealing. |

---

## 26. Lifecycle / State Machine

```text
CREATED
  → LOADING  (load_* started)
  → READY    (load success)
  → RELOADING (reload started from READY/DEGRADED)
  → READY | DEGRADED
  → CLOSED   (close)
```

| Transition | Allowed |
|---|---|
| CREATED → LOADING | yes |
| LOADING → READY | yes |
| LOADING → DEGRADED | yes (kept previous? only if previous existed; first load failure → DEGRADED/UNHEALTHY without catalog) |
| READY → RELOADING | yes |
| RELOADING → READY/DEGRADED | yes |
| * → CLOSED | yes (terminal) |
| CLOSED → * | no (`IL.STATE.CLOSED`) |

---

## 27. Event Bus Topics

| Topic | Payload | When |
|---|---|---|
| `market.instruments.catalog.loaded` | catalog summary + statistics | successful seal |
| `market.instruments.catalog.failed` | error code/message + statistics | load failure |

Publish only when `publish_events=True` and `event_bus` injected. Publish failures are isolated and must not roll back a successful seal.

---

## 28. Integration with Downstream Consumers

### 28.1 Market Data Streaming

```python
catalog = loader.load_from_file("instruments_nfo.csv")
descriptors = loader.project_descriptors(
    "NIFTY",
    expiry=None,          # nearest
    spot=24512.0,
    strikes_each_side=5,
    include_futures=True,
    include_spot=True,
)
streaming_engine.register_instruments(descriptors)
```

### 28.2 Kite WebSocket

```python
subs = loader.project_subscriptions("NIFTY", spot=24512.0, strikes_each_side=5)
ws_client.set_instruments(subs)
ws_client.apply_subscriptions()
```

### 28.3 Strategy / Risk / Order / Position / Portfolio / APME

These engines perform read-only lookups:

- lot size for quantity sizing
- token resolution for order legs
- expiry calendars for strategy eligibility
- strike grids for structure construction

They must not parse CSV or download masters directly.

### 28.4 System Orchestrator

Bootstrap sequence (illustrative):

1. Project `InstrumentLoaderConfig` from Application Configuration.
2. Construct `InstrumentLoader` with injected master client.
3. `load_from_cache()` or `load_from_broker()`.
4. Publish health into orchestrator aggregate.
5. Project descriptors/subscriptions into streaming + websocket components.
6. On daily boundary, `reload()`.

### 28.5 Market Data Engine

May own the scheduling of reload relative to market open, but the loader remains the only parse/index authority.

---

## 29. Testing Requirements

### 29.1 Unit test file

`tests/test_instrument_loader.py` — required.

Target: **≥ 95%** coverage of `broker/instrument_loader.py`.

### 29.2 Mandatory test categories

| Category | Examples |
|---|---|
| Config validation | empty underlyings, duplicates, bad strike step, cache path |
| CSV parsing | happy path, missing columns, malformed rows, BOM |
| Normalization | aliases, expiry formats, role mapping |
| Validation | invalid token/strike/expiry/option type/exchange |
| Duplicates | KEEP_FIRST / KEEP_LAST / REJECT |
| Expiry filter | drop past, keep today, keep future |
| Catalog indexes | token/symbol/underlying/expiry/strike consistency |
| Lookups | hit/miss/rejected paths |
| Queries | nearest/weekly/monthly/ATM/ITM/OTM/nearest strike/lot size |
| Projections | descriptors + subscriptions field compatibility |
| Cache | write/read/stale/corrupt |
| Serialization | round-trip + malformed + unsupported version |
| Concurrency | readers during reload; single-flight load |
| Determinism | identical inputs → identical catalog JSON |
| Boundaries | no kiteconnect/KiteTicker/place_order imports; no hardcoded tokens |
| Catalog parity | primary/secondary sets match websocket + streaming modules |
| Performance | smoke benchmarks under pytest markers |

### 29.3 Fixture policy

- Tests use local CSV fixtures under `tests/fixtures/instruments/` (small sliced masters).
- No live broker download in unit tests.
- Fake `InstrumentMasterClient` returns fixture rows.

### 29.4 Contract tests

| Test | Assertion |
|---|---|
| `test_underlying_catalog_parity` | loader/websocket/streaming primary+secondary frozensets equal |
| `test_descriptor_projection_field_parity` | projected descriptor field names cover streaming `InstrumentDescriptor` required fields |
| `test_subscription_projection_field_parity` | projected subscription field names cover websocket `SubscriptionInstrument` required fields |
| `test_no_hardcoded_instrument_tokens` | grep module source for long numeric token literals / sole-path spot keys |

### 29.5 Static compliance grep

CI must fail if `broker/instrument_loader.py` contains:

- `kiteconnect`
- `KiteTicker`
- `place_order`
- `generate_session`
- hardcoded sole-path strings `"NSE:NIFTY 50"` used as the only resolution source inside loader logic (alias maps for **names** are allowed; token integers are not)

### 29.6 Performance tests

Marked `@pytest.mark.performance` (optional in default CI):

- parse+seal 20k synthetic rows < 1s
- 10k token lookups < 50ms total

---

## 30. Implementation Checklist

1. Create `broker/instrument_loader.py` with constants, enums, frozen models, exceptions.
2. Implement `InstrumentLoaderConfig` validation.
3. Implement `InstrumentCsvParser` and JSON loader.
4. Implement normalization + underlying alias resolution (names only).
5. Implement `InstrumentRecordValidator` (VAL-IL-*).
6. Implement `DuplicateResolver` policies.
7. Implement `ExpiryFilter`.
8. Implement `CatalogIndexBuilder` and sealed `InstrumentCatalog`.
9. Implement `InstrumentLoader` lifecycle + loaders.
10. Implement lookup APIs returning `LookupResult`.
11. Implement query APIs (expiry/ATM/ITM/OTM/strike/lot).
12. Implement `project_descriptors` / `project_subscriptions`.
13. Implement cache read/write with checksum + atomic replace.
14. Implement health + statistics.
15. Implement serializers.
16. Implement optional Event Bus publish.
17. Add `tests/test_instrument_loader.py` (≥ 95% coverage).
18. Add fixture CSV slices and contract/parity tests.
19. Update `CHANGELOG.md` and cross-link from `market_data_streaming.md` Appendix K / `kite_websocket.md`.
20. Run static compliance greps in CI.

---

## 31. Definition of Done

This module is done when **all** of the following are true:

1. `broker/instrument_loader.py` exists as a complete production implementation (no placeholder modules).
2. `tests/test_instrument_loader.py` exists with ≥ 95% coverage.
3. Public models `InstrumentRecord`, `InstrumentCatalog`, `CatalogHealth`, `CatalogStatistics`, `LookupResult` are implemented as frozen dataclasses with Google-style docstrings.
4. Loader can download via injected client, load local CSV, and load in-memory rows through one pipeline.
5. Validation covers duplicate detection, invalid strike, missing expiry, invalid option type, invalid exchange, invalid instrument token.
6. Expired contracts are removed when `drop_expired=True`.
7. Immutable catalog + cached indexes support all listed lookup/query APIs.
8. Projections satisfy streaming + websocket consumer contracts.
9. Health/statistics expose load duration, record counts, duplicate counts, expiry counts.
10. Serialization is versioned JSON.
11. Thread-safe reload with concurrent readers verified by tests.
12. Deterministic seal verified by tests.
13. Architecture boundaries enforced: **MUST NOT** connect WebSocket, stream ticks, evaluate strategies, calculate risk, or place orders.
14. No hardcoded instrument tokens as sole-path identity.
15. Catalog parity contract with websocket/streaming modules passes.
16. Documentation cross-links updated.

---

## 32. Non-Goals / Explicit Non-Changes

1. Do not redesign THETA AI TRADER architecture or engine pipeline order.
2. Do not merge this module into `kite_broker.py` or `market_data_streaming.py`.
3. Do not add order/risk/strategy APIs to this module.
4. Do not scrape unofficial instrument sources.
5. Do not compute live prices, Greeks, or IV.
6. Do not own WebSocket subscribe/unsubscribe.
7. Do not make Application Configuration read instrument CSV.
8. Do not hardcode production instrument tokens.
9. Do not introduce a second competing catalog in another module for v1.0.
10. Equity F&O may be schema-ready but remains disabled by default.

---

## Appendix A — Worked Example: Load NFO CSV and Project NIFTY Chain

```python
from pathlib import Path
from datetime import datetime, timezone
from broker.instrument_loader import (
    InstrumentLoader,
    InstrumentLoaderConfig,
    default_instrument_loader_config,
)
from config.application_configuration import EnvironmentProfile

config = InstrumentLoaderConfig(
    environment_profile=EnvironmentProfile.PAPER,
    enabled_underlyings=("NIFTY", "BANKNIFTY", "SENSEX"),
    enabled_exchanges=("NSE", "NFO", "BSE", "BFO"),
    drop_expired=True,
    cache_enabled=False,
    runner_kind="paper",
)
loader = InstrumentLoader(
    config,
    clock=lambda: datetime(2026, 8, 5, 3, 30, tzinfo=timezone.utc),
    id_factory=lambda: "catalog-fixed",
)
catalog = loader.load_from_file(Path("tmp/instruments_nfo.csv"))
assert catalog.record_count > 0

nearest = loader.find_nearest_expiry("NIFTY")
assert nearest.status.value == "HIT"
expiry = nearest.primary.expiry  # type: ignore[union-attr]

atm = loader.resolve_atm_strike("NIFTY", spot=24512.0, expiry=expiry)
atm_opts = loader.query_atm_options("NIFTY", spot=24512.0, expiry=expiry)
assert len(atm_opts.records) == 2  # CE + PE

descriptors = loader.project_descriptors(
    "NIFTY",
    expiry=expiry,
    spot=24512.0,
    strikes_each_side=2,
    include_futures=True,
    include_spot=True,
)
# Pass descriptors to MarketDataStreamingEngine.register_instruments(...)
```

---

## Appendix B — Example Instrument CSV Header and Rows (illustrative)

```csv
instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange
256265,0,NIFTY 50,NIFTY 50,0,,,0.05,1,INDEX,INDICES,NSE
12345678,0,NIFTY2580724500CE,NIFTY,0,2026-08-07,24500,0.05,75,CE,NFO-OPT,NFO
12345679,0,NIFTY2580724500PE,NIFTY,0,2026-08-07,24500,0.05,75,PE,NFO-OPT,NFO
12345680,0,NIFTY25AUGFUT,NIFTY,0,2026-08-28,,0.05,75,FUT,NFO-FUT,NFO
```

Note: tokens above are **illustrative fixture values** for documentation/tests only. Production code must not embed them as sole-path constants.

---

## Appendix C — Full Seal Pipeline Pseudocode

```python
def seal_catalog(rows, config, clock, id_factory, source_kind, source_uri):
    t0 = perf_counter()
    normalized = [normalize_row(r, config) for r in rows]
    t1 = perf_counter()
    kept, discarded_invalid = [], []
    for row in normalized:
        issues = validate_row(row, config)
        if issues:
            discarded_invalid.extend(issues)
        else:
            kept.append(row)
    kept_sorted = stable_sort(kept)
    unique, discarded_dupes = resolve_duplicates(kept_sorted, config.duplicate_policy)
    as_of = clock().astimezone(ZoneInfo(config.expiry_timezone)).date().isoformat()
    active, discarded_expired = filter_expired(unique, as_of, config.drop_expired)
    if len(active) > config.max_records:
        raise InstrumentLoaderConfigurationError(...)  # or truncate policy if explicitly enabled
    records = tuple(to_instrument_record(r, as_of) for r in canonical_sort(active))
    if config.require_non_empty_catalog and not records:
        raise InstrumentValidationError(code="IL.VALIDATION.EMPTY_CATALOG", ...)
    t2 = perf_counter()
    indexes = build_indexes(records)
    t3 = perf_counter()
    stats = CatalogStatistics(
        as_of=clock(),
        source_kind=source_kind,
        load_duration_ms=(t3 - t0) * 1000,
        parse_duration_ms=(t1 - t0) * 1000,
        validate_duration_ms=(t2 - t1) * 1000,
        index_duration_ms=(t3 - t2) * 1000,
        raw_row_count=len(rows),
        retained_record_count=len(records),
        discarded_invalid_count=len(discarded_invalid),
        discarded_duplicate_count=len(discarded_dupes),
        discarded_expired_count=len(discarded_expired),
        ...
    )
    return InstrumentCatalog(
        catalog_id=id_factory(),
        schema_version=INSTRUMENT_LOADER_SCHEMA_VERSION,
        loaded_at=clock(),
        as_of_date=as_of,
        source_kind=source_kind,
        source_uri=source_uri,
        enabled_underlyings=config.enabled_underlyings,
        enabled_exchanges=config.enabled_exchanges,
        records=records,
        record_count=len(records),
        indexes=indexes,
        statistics=stats,
        metadata=MappingProxyType(dict(config.metadata)),
    )
```

---

## Appendix D — Concurrency Sketches

### D.1 Atomic catalog swap

```text
reader:
  catalog = atomic_load(current_catalog_ref)
  if catalog is None: raise NOT_READY
  return catalog.indexes.by_token.get(token)

writer(load):
  acquire load_lock (single-flight)
  new_catalog = seal_catalog(...)
  atomic_store(current_catalog_ref, new_catalog)
  release load_lock
```

### D.2 Readers during reload

Old catalog remains fully usable until swap. No reader observes half-built indexes.

### D.3 Forbidden patterns

- Mutating `records` list in place
- Sharing mutable dict indexes
- Performing CSV parse inside `get_by_token`

---

## Appendix E — Glossary

| Term | Definition |
|---|---|
| Instrument master | Broker-published list of tradable instruments and metadata |
| InstrumentRecord | Normalized validated platform row |
| InstrumentCatalog | Immutable sealed catalog + indexes |
| Quote key | `EXCHANGE:SYMBOL` identity string |
| Projection | Conversion of catalog rows into consumer DTOs |
| Weekly expiry | Non-month-end expiry heuristic for index options |
| Monthly expiry | Last expiry in a calendar month for an underlying |
| ATM | Strike nearest to spot under strike-step + available strikes |
| Soft-discard | Row rejected without aborting the whole load |
| Seal | Freeze records/indexes/statistics into an immutable catalog |

---

## Appendix F — Migration Notes

| Legacy pattern | v1.0 replacement |
|---|---|
| Ad-hoc `kite.instruments("NFO")` inside scripts | `InstrumentLoader.load_from_broker()` |
| Re-parsing CSV in streaming/websocket modules | `project_descriptors` / `project_subscriptions` |
| Hardcoded NIFTY token constants in engines | Catalog lookup / projection |
| market_data_streaming Appendix K "external resolver" | **This module** |

Legacy research scripts under the repo root may continue to exist but are non-authoritative.

---

## Appendix G — Performance Benchmark Targets

| Benchmark | Target |
|---|---|
| `bench_parse_seal_20k` | < 1.0s |
| `bench_token_lookup_10k` | < 0.05s |
| `bench_project_nifty_chain` | < 0.02s |
| `bench_concurrent_lookups_32t` | 0 errors |

---

## Appendix H — Non-Goals Confirmation Checklist

- [ ] No WebSocket imports/clients
- [ ] No tick streaming / quote book
- [ ] No strategy evaluation
- [ ] No risk calculation
- [ ] No order placement
- [ ] No hardcoded sole-path instrument tokens
- [ ] No `.env` loading
- [ ] No MarketSnapshot assembly

---

## Appendix I — Failure Scenario Matrix

| Scenario | Result |
|---|---|
| Broker download timeout | `IL.IO.BROKER_FETCH_FAILED`; previous catalog retained |
| CSV missing columns | `IL.PARSE.MISSING_COLUMNS` |
| All rows expired | empty retained set → fail if `require_non_empty_catalog` |
| Duplicate tokens with REJECT policy | load aborted |
| Cache corrupt | `IL.IO.CACHE_CORRUPT`; fallback to file/broker |
| Lookup before load | `IL.STATE.NOT_READY` |
| Closed loader call | `IL.STATE.CLOSED` |
| Underlying enabled but absent in master | health `UNDERLYING_MISSING`; catalog may still READY/DEGRADED |

---

## Appendix J — Configuration Defaults by Profile

### Development

```text
allow_experimental_underlyings = True
allow_equity_fo = False
drop_expired = True
cache_enabled = True (if directory provided)
prefer_cache_before_download = True
require_non_empty_catalog = False
publish_events = False
duplicate_policy = KEEP_FIRST_STABLE
```

### Paper

```text
allow_experimental_underlyings = False
allow_equity_fo = False
cache_enabled = True
cache_directory required
require_non_empty_catalog = True
publish_events = True
```

### Production

```text
allow_experimental_underlyings = False
allow_equity_fo = False
cache_enabled = True
cache_directory required
cache_max_age_seconds = 86400
require_non_empty_catalog = True
publish_events = True
duplicate_policy = KEEP_FIRST_STABLE
```

---

## Appendix K — Relationship to market_data_streaming Appendix K

`docs/specifications/market_data_streaming.md` Appendix K describes an external resolver that converts instrument master rows into `InstrumentDescriptor` tuples.

**Ownership assignment for v1.0:**

| Concern | Owner |
|---|---|
| Download/load/parse/validate/index master | `broker/instrument_loader.py` (**this module**) |
| Project descriptors for a strike window | `InstrumentLoader.project_descriptors` |
| Register descriptors into quote book/assembly | `MarketDataStreamingEngine.register_instruments` |
| Consume ticks / assemble snapshots | `MarketDataStreamingEngine` |

This removes ambiguity: streaming remains assembly-only; loader remains identity-only.

---

## Appendix L — Related Documents

- `docs/specifications/kite_websocket.md`
- `docs/specifications/kite_authentication.md`
- `docs/specifications/kite_broker.md`
- `docs/specifications/market_data_streaming.md`
- `docs/specifications/market_data_engine.md`
- `docs/specifications/market_snapshot.md`
- `docs/specifications/application_configuration.md`
- `docs/specifications/system_orchestrator.md`
- `docs/specifications/strategy_engine.md`
- `docs/specifications/risk_engine.md`
- `docs/specifications/order_manager.md`
- `docs/specifications/position_manager.md`
- `docs/specifications/portfolio_manager.md`
- `docs/specifications/apme.md`
- `docs/specifications/event_bus.md`

---

## Appendix M — Implementation Checklist (engineer, expanded)

1. Mirror coding standards: type hints, frozen dataclasses, Google docstrings, PEP 8.
2. Keep engines/components stateless where specified; loader facade holds only lifecycle + current catalog ref.
3. Ensure `normalize_underlying_name` / `classify_underlying_tier` parity helpers exist.
4. Implement deterministic sort helpers used by seal + indexes + duplicates.
5. Wire `default_instrument_loader_config(profile)`.
6. Add `__all__` exports for public API surface.
7. Ensure no circular imports with streaming/websocket (use `TYPE_CHECKING` for consumer types if needed, or duck-typed namespaces).
8. Add CHANGELOG entry: "Add instrument loader specification and module".
9. Add CI grep compliance job.
10. Verify tests pass on Python 3.9+ as used by the repository.

---

## Appendix N — Performance Tuning Guidance

| Symptom | Likely cause | Tuning lever |
|---|---|---|
| Slow startup | Parsing full multi-exchange dump every launch | Enable cache; prefer cache before download |
| High memory | Retaining too many underlyings/exchanges | Narrow `enabled_underlyings` / exchanges |
| Slow projection | strikes_each_side too large | Reduce window; preselect expiry |
| Reload stalls readers | Lock held during parse | Ensure parse occurs before swap; readers use old ref |
| High discard ratio | Wrong underlying aliases | Extend config-injected alias map (names only) |

---

## Appendix O — Example CatalogHealth JSON (illustrative)

```json
{
  "schema_version": "1.0.0",
  "report_id": "health-1",
  "as_of": "2026-08-05T03:45:00Z",
  "lifecycle_state": "READY",
  "overall_health": "HEALTHY",
  "has_catalog": true,
  "catalog_id": "catalog-fixed",
  "record_count": 18422,
  "enabled_underlyings": ["NIFTY", "BANKNIFTY", "SENSEX"],
  "underlyings_with_records": ["NIFTY", "BANKNIFTY", "SENSEX"],
  "underlyings_missing_records": [],
  "seconds_since_load": 12.4,
  "issues": [],
  "statistics": {
    "schema_version": "1.0.0",
    "as_of": "2026-08-05T03:44:47Z",
    "source_kind": "LOCAL_CSV",
    "load_duration_ms": 842.1,
    "parse_duration_ms": 510.0,
    "validate_duration_ms": 220.4,
    "index_duration_ms": 111.7,
    "raw_row_count": 92000,
    "retained_record_count": 18422,
    "discarded_invalid_count": 12,
    "discarded_duplicate_count": 2,
    "discarded_expired_count": 73564,
    "discarded_underlying_count": 0,
    "discarded_exchange_count": 0,
    "discarded_equity_fo_count": 0,
    "option_count": 18000,
    "future_count": 40,
    "spot_count": 3,
    "volatility_count": 1,
    "expiry_count": 18,
    "underlying_counts": {
      "NIFTY": 8200,
      "BANKNIFTY": 6100,
      "SENSEX": 4122
    },
    "last_error_code": null
  },
  "metadata": {
    "runner_kind": "paper"
  }
}
```

---

## Appendix P — Security Review Prompts

1. Does any fixture embed live access tokens in catalog metadata?
2. Can cache files written by this module contain secrets?
3. Are broker download errors logged without dumping Authorization headers?
4. Are path operations free of shell=True usage?
5. Do alias maps contain only names (no tokens) in source?

---

## Appendix Q — Acceptance Scenarios (Definition of Done narrative)

1. **Cold start from CSV:** Loader seals NIFTY/BANKNIFTY/SENSEX catalog; `get_spot("NIFTY")` hits; nearest expiry query returns a future date.
2. **Projection into streaming:** Descriptors register successfully; streaming assembles snapshots without CSV knowledge.
3. **Projection into websocket:** Subscription instruments apply without hardcoded tokens.
4. **Expiry day behaviour:** Contracts expiring today remain; yesterday removed.
5. **Duplicate token:** KEEP_FIRST retains stable winner; stats show discard.
6. **Concurrent reload:** 16 readers continue HIT lookups while reload swaps catalog.
7. **Boundary grep:** CI greps pass for forbidden imports/symbols.
8. **Parity:** Underlying frozensets match websocket/streaming.

---

## Appendix R — Module Constants (reference)

```python
INSTRUMENT_LOADER_VERSION = "1.0.0"
INSTRUMENT_LOADER_SCHEMA_VERSION = "1.0.0"
PRODUCER_NAME = "broker.instrument_loader"

SUPPORTED_PRIMARY_UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})
SUPPORTED_SECONDARY_UNDERLYINGS = frozenset({"FINNIFTY", "MIDCPNIFTY"})
SUPPORTED_INDEX_UNDERLYINGS = (
    SUPPORTED_PRIMARY_UNDERLYINGS | SUPPORTED_SECONDARY_UNDERLYINGS
)
SUPPORTED_EXCHANGES = frozenset({"NSE", "NFO", "BSE", "BFO", "MCX"})
SUPPORTED_OPTION_TYPES = frozenset({"CE", "PE"})

TOPIC_CATALOG_LOADED = "market.instruments.catalog.loaded"
TOPIC_CATALOG_FAILED = "market.instruments.catalog.failed"

DEFAULT_MAX_RECORDS = 500_000
DEFAULT_CACHE_MAX_AGE_SECONDS = 86400.0
DEFAULT_STRIKE_STEP = 50.0
```

---

## Appendix S — Error-to-Outcome Mapping (quick reference)

| Failure | Load outcome | Catalog pointer | Health |
|---|---|---|---|
| Config invalid at construct | exception | n/a | n/a |
| First load IO failure | exception or DEGRADED | none | UNHEALTHY |
| Reload IO failure | previous retained | previous | DEGRADED + LOAD_FAILED |
| Soft validation discards | success | new | HEALTHY/DEGRADED by coverage |
| Empty retained + required | exception | previous/none | UNHEALTHY |
| Lookup miss | LookupResult.MISS | unchanged | unchanged |

---

## Appendix T — Weekly/Monthly Expiry Classification Examples

For an underlying with option expiries:

```text
2026-08-07, 2026-08-14, 2026-08-21, 2026-08-28, 2026-09-03, 2026-09-10, 2026-09-24
```

Monthly set = last expiry in each month = `{2026-08-28, 2026-09-24}`  
Weekly set = all others.

Nearest weekly as-of `2026-08-05` = `2026-08-07`.  
Nearest monthly as-of `2026-08-05` = `2026-08-28`.

---

## Appendix U — ITM/OTM Depth Examples

Spot = `24512`, ATM = `24500`, step = `50`, expiry strikes include `24400..24600`.

| Query | option_type | depth | Selected strikes |
|---|---|---|---|
| ATM | CE/PE | n/a | 24500 |
| ITM | CE | 1 | 24450 |
| ITM | CE | 2 | 24450, 24400 |
| OTM | CE | 1 | 24550 |
| ITM | PE | 1 | 24550 |
| OTM | PE | 1 | 24450 |

---

## Appendix V — Projection Field Map to InstrumentDescriptor

| Descriptor field | Source InstrumentRecord field |
|---|---|
| `instrument_token` | `instrument_token` |
| `underlying` | `underlying` |
| `quote_key` | `quote_key` |
| `exchange` | `exchange` |
| `tradingsymbol` | `tradingsymbol` |
| `instrument_kind` | `instrument_type` (INDEX/CE/PE/FUT/VIX) |
| `instrument_role` | `instrument_role` |
| `strike` | `strike` |
| `option_type` | `option_type` |
| `expiry` | `expiry` |
| `lot_size` | `lot_size` |
| `tick_size` | `tick_size` |
| `support_tier` | `support_tier` |
| `metadata` | subset of `metadata` (non-secret) |

---

## Appendix W — Projection Field Map to SubscriptionInstrument

| Subscription field | Source |
|---|---|
| `instrument_token` | `instrument_token` |
| `underlying` | `underlying` |
| `tradingsymbol` | `tradingsymbol` |
| `exchange` | `exchange` |
| `instrument_kind` | `instrument_type` |
| `mode` | caller argument / default None |

---

## Appendix X — Future NSE F&O Stocks Extension Path

When enabling equity F&O later:

1. Set `allow_equity_fo=True`.
2. Provide `enabled_equity_underlyings=("RELIANCE","TCS",...)` via Application Configuration projection.
3. No schema change to `InstrumentRecord` required (`support_tier=EQUITY_FO`).
4. Strike steps supplied via `strike_step` map per symbol.
5. Streaming/websocket catalogs remain allowlist-driven; equity names must be explicitly enabled there too before trading use (separate change controlled by those specs — not silent expansion).

---

## Appendix Y — Open Implementation Notes (non-blocking)

1. Exact broker expiry string variants are adapter-tested; normalizer must accept the variants observed in Zerodha CSV dumps used by the team fixtures.
2. INDIA VIX association is config-map driven because the VIX row's `name` may not equal `NIFTY`.
3. If consumer DTO classes cannot be imported lightly, projection may return `SimpleNamespace`/frozen local DTOs with identical fields; contract tests enforce field parity.

---

## Appendix Z — Changelog

| Version | Date | Notes |
|---|---|---|
| 1.0.0 | 2026-08-05 | Initial complete software engineering specification for `broker/instrument_loader.py`. |

---

**End of specification.**
