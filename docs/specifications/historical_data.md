# Historical Data — Software Engineering Specification

| Field | Value |
|---|---|
| **Module** | `broker/historical_data.py` |
| **Document version** | 1.0.0 |
| **Status** | Draft — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-05 |

---

## 1. Purpose

`broker/historical_data.py` defines the **sole historical OHLCV market-data component** for THETA AI TRADER v1.0.

The module answers a question that no other frozen module answers: *"Once Application Configuration has projected which underlyings and lookbacks are enabled, who fetches historical candles from Zerodha (or loads them from cache), validates candle integrity, detects gaps and duplicates, normalizes timestamps, applies corporate-action awareness where applicable, and serves immutable historical series — without streaming live ticks, without computing indicators, and without performing any trading logic?"*

It is the **only** module permitted to:

1. Fetch historical OHLCV candles from Zerodha via an injected REST/broker client boundary (never by embedding `kiteconnect` SDK calls as a sole path).
2. Load cached candles from memory and disk under a documented cache contract.
3. Validate candle integrity (OHLC consistency, volume, timestamp order, duplicates, missing bars).
4. Detect and classify gaps relative to the requested timeframe and session calendar.
5. Normalize all candle timestamps under documented IST/UTC rules.
6. Apply corporate-action awareness hooks where applicable (adjustment metadata; never invent adjusted prices without an explicit policy).
7. Maintain a **thread-safe** historical cache (`HistoricalCache`) with deterministic refresh policy.
8. Serve immutable `HistoricalCandle` / `HistoricalSeries` objects to Market Regime Engine, Indicator Engine, Strategy Evaluation Engine, APME, Dashboard, Paper Trading, and Backtesting.
9. Expose health and fetch statistics for System Orchestrator aggregation.

It is **not** a WebSocket client. It is **not** a live tick streamer. It is **not** an indicator calculator. It is **not** a strategy, risk, order, position, or portfolio component. It is **historical market-data plumbing** — the deterministic, thread-safe, immutable source of truth for "what did this instrument's OHLCV look like over a past interval?"

### 1.1 The gap this module fills

Multiple frozen or previously specified modules deliberately refuse to own validated historical series:

| Frozen / specified module | Explicit non-responsibility |
|---|---|
| `broker/kite_websocket.py` | Live transport only; never fetches or caches historical candles. |
| `broker/market_data_streaming.py` | Continuous live snapshot assembly only; never historical backfill. |
| `broker/kite_authentication.py` | Authentication/session only. |
| `broker/zerodha/kite_broker.py` | May expose `fetch_historical` as a REST transport primitive returning raw broker mappings; does **not** own validation, gap detection, series sealing, or multi-tier caching. |
| `broker/instrument_loader.py` | Instrument identity catalog only; never fetches OHLCV. |
| `market_data/market_data_engine.md` R12 | Optional raw REST historical fetch returning broker-native payloads **without** validation/caching/series APIs — superseded for platform consumers by **this** module. |
| Indicator / Regime / Strategy engines | Consume historical series; must not fetch or cache broker candles themselves. |

Nobody in the frozen/specified architecture currently owns:

- A single authoritative fetch → validate → gap-detect → cache → serve pipeline for historical OHLCV.
- Immutable `HistoricalCandle` / `HistoricalSeries` types shared by Regime, Indicator, Strategy Evaluation, APME, Dashboard, Paper, and Backtesting.
- Memory + disk cache with deterministic refresh and eviction policy.
- Session-aware missing-candle / gap classification for Indian index F&O calendars.
- Health and latency/error-rate statistics for historical fetch paths.

`broker/historical_data.py` closes this gap. It is the mandatory historical-data layer between "broker REST can return raw candles" and "every analytical/backtesting consumer can pull a validated immutable series."

### 1.2 Pipeline placement

```text
[config/application_configuration.py]
    MarketDataConfiguration / HistoricalDataPolicy projection
    (enabled_underlyings, timeframes, cache dirs, lookbacks, session calendar)
              │
              ▼
[broker/kite_authentication.py] ──► BrokerSession (required for live fetch)
              │
              ▼
[broker/zerodha/kite_broker.py] (injected HistoricalDataClient boundary)
    fetch_historical(token, from, to, interval, continuous)
              │
              ▼
[broker/instrument_loader.py] (optional resolver for symbol → token)
              │
              ▼
[broker/historical_data.py]                              ← THIS MODULE
    ┌──────────────────────────────────────────────────────────────────┐
    │ HISTORICAL DATA PIPELINE                                          │
    │   fetch_range() / fetch_latest_n() / fetch_session() /            │
    │   fetch_rolling_window() / load_from_cache()                      │
    │     → resolve instrument token (token or symbol)                  │
    │     → check memory cache → disk cache → broker fetch              │
    │     → normalize timestamps (IST/UTC)                              │
    │     → validate candles (VAL-HD-*)                                 │
    │     → detect gaps / missing bars (GAP-HD-*)                       │
    │     → apply corporate-action policy hooks (CA-HD-*)               │
    │     → seal immutable HistoricalSeries                             │
    │     → update HistoricalCache (memory + optional disk)             │
    │     → emit HistoricalHealth / HistoricalStatistics                │
    └──────────────────────────────────────────────────────────────────┘
              │
              ├──────────────► Market Regime Engine
              ├──────────────► Indicator Engine
              ├──────────────► Strategy Evaluation Engine
              ├──────────────► APME
              ├──────────────► Dashboard
              ├──────────────► Paper Trading
              └──────────────► Backtesting
```

### 1.3 Architecture freeze note

The platform architecture is **FROZEN** for v1.0. This module does **not**:

- Own `KiteTicker` or stream live ticks — exclusive to `broker/kite_websocket.py` / `broker/market_data_streaming.py` (Rule BOUNDARY-HD-001).
- Assemble continuous `MarketSnapshot` objects from the live quote book — exclusive to `broker/market_data_streaming.py` (Rule BOUNDARY-HD-002).
- Perform OAuth or token persistence — exclusive to `broker/kite_authentication.py` (Rule BOUNDARY-HD-003).
- Replace `broker/zerodha/kite_broker.py` REST transport. Live fetch uses an **injected** `HistoricalDataClient` protocol whose production adapter may wrap `KiteBrokerClient.fetch_historical` (Rule BOUNDARY-HD-004).
- Replace `broker/instrument_loader.py`. Symbol→token resolution is delegated to an injected resolver or explicit token arguments; this module never parses the instrument master CSV (Rule BOUNDARY-HD-005).
- Calculate technical indicators (EMA, RSI, VWAP, etc.) — exclusive to Indicator Engine (Rule BOUNDARY-HD-006).
- Detect market regimes, score strategies, compute risk, size positions, manage positions, or place/modify/cancel orders (Rule BOUNDARY-HD-007).
- Load Application Configuration files, `.env` files, or environment variables directly. It accepts an already-projected `HistoricalDataConfig` (Rule BOUNDARY-HD-008).
- Hardcode instrument tokens or sole-path spot quote keys as the only resolution path (Rule BOUNDARY-HD-009).
- Become a coordinated trading engine or gain a `run_forever` trading loop (Rule BOUNDARY-HD-010).

### 1.4 Goals

1. Provide a **single historical OHLCV component** for the entire platform.
2. Support **primary** underlyings (`NIFTY`, `BANKNIFTY`, `SENSEX`) and **secondary** underlyings (`FINNIFTY`, `MIDCPNIFTY`) without hardcoding tokens.
3. Reserve an explicit **future extension path** for NSE F&O stocks without redesigning the candle model.
4. Support timeframes: `1m`, `3m`, `5m`, `10m`, `15m`, `30m`, `60m`, `1d`.
5. Fetch by **instrument token** or **trading symbol** (symbol requires injected resolver).
6. Serve **date-range**, **latest N**, **session**, and **rolling-window** queries.
7. Validate missing/duplicate candles, timestamp order, OHLC consistency, and volume.
8. Detect gaps with deterministic classification.
9. Normalize timestamps with explicit IST ↔ UTC rules; naive datetimes are never accepted at the public boundary.
10. Maintain **memory + disk** caches with documented refresh/eviction policy.
11. Expose **HistoricalHealth** and **HistoricalStatistics** (latency, error rate, cache hits).
12. Provide **versioned JSON serialization**.
13. Be **thread-safe** and **deterministic**.
14. Use **Google-style docstrings** and **immutable dataclasses** (`frozen=True`).
15. Reach **≥ 95% unit test coverage** on `broker/historical_data.py`.
16. **Never** stream live ticks, evaluate strategies, calculate indicators, place orders, or execute trades.

### 1.5 Success criteria

- Given a valid NIFTY index token and a date range, `HistoricalDataService.fetch_range(...)` returns a sealed `HistoricalSeries` of immutable `HistoricalCandle` objects with timezone-aware timestamps and `quality.validation_status` of `VALID` or `PARTIAL` per policy.
- `fetch_latest_n(token, timeframe=MINUTE_5, n=100)` returns exactly ≤ 100 candles ending at the latest available bar, deterministically ordered ascending by timestamp.
- Feeding broker rows with inverted high/low fails validation with `HD.VALIDATION.OHLC_INCONSISTENT` (or soft-discards under non-strict policy with statistics incremented).
- Duplicate timestamps are collapsed under the configured duplicate policy; ascending order is guaranteed in the sealed series.
- Concurrent readers calling `get_cached_series` while a background fetch refreshes the cache never observe torn candle arrays.
- Grep of the module finds **zero** references to `KiteTicker`, `place_order`, indicator formulas (EMA/RSI), strategy scoring, or position management.
- Unit coverage ≥ 95% on `broker/historical_data.py`.

### 1.6 Relationship to other modules

| Module | Relationship |
|---|---|
| `config/application_configuration.py` | **Upstream policy.** Projects lookbacks, timeframes, cache paths into `HistoricalDataConfig`. |
| `broker/kite_authentication.py` | **Optional upstream.** Supplies `BrokerSession` used by the injected broker client. |
| `broker/zerodha/kite_broker.py` | **Transport adapter.** Production `HistoricalDataClient` may wrap `fetch_historical`. |
| `broker/instrument_loader.py` | **Optional resolver.** Symbol→token resolution may use catalog lookups; this module never owns the master. |
| `broker/kite_websocket.py` | **Peer, not dependency.** Live ticks are out of scope. |
| `broker/market_data_streaming.py` | **Peer, not dependency.** Live snapshots are out of scope. |
| Market Regime Engine | **Downstream consumer.** Pulls validated series for regime features. |
| Indicator Engine | **Downstream consumer.** Pulls series; computes indicators itself. |
| Strategy Evaluation Engine | **Downstream consumer.** Uses historical context for evaluation/backtest windows. |
| APME | **Downstream consumer.** May use recent historical bars for adaptive management context. |
| Dashboard / Paper Trading / Backtesting | **Downstream consumers.** Read-only series access. |
| System Orchestrator | **Lifecycle / health consumer.** |
| `core/event_bus.py` | **Optional transport** for `market.historical.*` topics. |

### 1.7 Distinction from adjacent modules

| Concern | `kite_broker.py` | `market_data_engine.py` | `historical_data.py` (this) | Indicator Engine |
|---|---|---|---|---|
| REST `historical_data` transport | **Yes** (primitive) | Optional raw passthrough | Consumes via injected client | No |
| Validate/gap-detect/seal series | No | No | **Yes** | No |
| Memory + disk candle cache | No | No | **Yes** | No |
| Compute EMA/RSI/etc. | No | No | No | **Yes** |
| Stream live ticks | Optional elsewhere | Yes (orchestrates) | No | No |
| Output type | raw mappings | raw mappings | `HistoricalSeries` | indicator outputs |

**Rule BOUNDARY-HD-011:** This module may import typing utilities and optionally import resolver types from `broker.instrument_loader` for symbol resolution helpers. It must never import `kiteconnect` directly, never import `KiteTicker`, and never import strategy/risk/order/indicator computation modules.

---

## 2. Responsibilities

`broker/historical_data.py` **is responsible for**:

| # | Responsibility | Description |
|---|---|---|
| R1 | **Broker historical fetch** | Fetch OHLCV via injected `HistoricalDataClient` for configured intervals. |
| R2 | **Token / symbol resolution** | Accept instrument token directly, or resolve trading symbol via injected resolver. |
| R3 | **Date-range fetch** | Serve candles for `[from_ts, to_ts]` inclusive of completed bars per policy. |
| R4 | **Latest-N fetch** | Serve the most recent N candles for a timeframe. |
| R5 | **Session fetch** | Serve candles belonging to a single trading session (IST calendar day / exchange session). |
| R6 | **Rolling-window fetch** | Serve candles covering a trailing duration (e.g., last 5 trading days). |
| R7 | **Candle normalization** | Map broker fields → `HistoricalCandle` with timezone-aware timestamps. |
| R8 | **Integrity validation** | OHLC consistency, non-negative volume/OI, finite prices, timestamp awareness. |
| R9 | **Duplicate handling** | Detect duplicate timestamps; apply deterministic policy. |
| R10 | **Ordering** | Guarantee ascending timestamp order in sealed series. |
| R11 | **Gap detection** | Classify missing bars vs expected session grid. |
| R12 | **Missing-candle policy** | Fill / mark / fail according to config (never invent prices silently in Production). |
| R13 | **Corporate-action awareness** | Attach adjustment metadata / apply explicit adjustment policy when provided. |
| R14 | **Memory cache** | Thread-safe in-process cache keyed by (token, timeframe, range signature). |
| R15 | **Disk cache** | Optional versioned on-disk cache with checksum and TTL. |
| R16 | **Refresh policy** | Deterministic stale/fresh rules for partial vs complete ranges. |
| R17 | **Health reporting** | Expose `HistoricalHealth`. |
| R18 | **Statistics** | Expose `HistoricalStatistics` (hits, misses, latency, errors). |
| R19 | **Error taxonomy** | Stable `HD.*` codes. |
| R20 | **Thread safety** | Protect cache mutations; return immutable series. |
| R21 | **Determinism** | Identical inputs + clock + config → identical sealed series. |
| R22 | **Serialization** | Versioned JSON for candle/series/health/statistics/cache manifests. |
| R23 | **Lifecycle management** | `CREATED` → `READY` → `DEGRADED` / `CLOSED`. |
| R24 | **Configuration validation** | Validate `HistoricalDataConfig` before fetch. |
| R25 | **Multi-underlying support** | Isolate cache/stats per underlying/instrument. |

---

## 3. Non-Responsibilities

| # | Non-responsibility | Owner instead |
|---|---|---|
| NR1 | **Stream live ticks / WebSocket** | `kite_websocket.py` / `market_data_streaming.py` |
| NR2 | **Assemble live MarketSnapshot** | `market_data_streaming.py` |
| NR3 | **Calculate indicators** | Indicator Engine |
| NR4 | **Detect market regimes** | Market Regime Engine |
| NR5 | **Evaluate strategies / score signals** | Strategy Evaluation Engine |
| NR6 | **Place or manage orders** | Order Manager / Execution Engine |
| NR7 | **Compute risk or size positions** | Risk Engine / Position Sizing |
| NR8 | **Manage open positions / APME decisions** | APME / Position Manager |
| NR9 | **Authenticate / persist tokens** | `kite_authentication.py` |
| NR10 | **Parse instrument master CSV** | `instrument_loader.py` |
| NR11 | **Hardcode instrument tokens** | Absolute prohibition |
| NR12 | **Load `.env` / YAML** | Application Configuration |
| NR13 | **Scrape unofficial candle sources** | Only broker REST + caches + injected rows |
| NR14 | **Invent corporate-action adjusted prices without policy** | Explicit CA policy or external vendor feed |
| NR15 | **Own dashboard rendering** | Dashboard consumers |

---

## 4. Supported Underlying Catalog

Validation allowlist only — never a hardcoded token table.

### 4.1 Primary

| Underlying | Tier |
|---|---|
| `NIFTY` | PRIMARY |
| `BANKNIFTY` | PRIMARY |
| `SENSEX` | PRIMARY |

### 4.2 Secondary

| Underlying | Tier |
|---|---|
| `FINNIFTY` | SECONDARY |
| `MIDCPNIFTY` | SECONDARY |

### 4.3 Future — NSE F&O stocks

Schema-ready via `allow_equity_fo` + `enabled_equity_underlyings`; disabled by default.

### 4.4 Constants

```python
SUPPORTED_PRIMARY_UNDERLYINGS   = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})
SUPPORTED_SECONDARY_UNDERLYINGS = frozenset({"FINNIFTY", "MIDCPNIFTY"})
SUPPORTED_INDEX_UNDERLYINGS     = PRIMARY | SECONDARY
```

### 4.5 Catalog rules

| Rule ID | Statement |
|---|---|
| CAT-HD-001 | Canonical names uppercase ASCII. |
| CAT-HD-002 | Catalog must remain identical to `instrument_loader` / `kite_websocket` / `market_data_streaming` (contract test `test_underlying_catalog_parity`). |
| CAT-HD-003 | No hardcoded `instrument_token` constants. |
| CAT-HD-004 | Underlying tags on series are informational; fetch identity is token-based. |

---

## 5. Supported Timeframes

### 5.1 Enumeration `CandleTimeframe`

| Enum | Wire / Kite interval | Duration |
|---|---|---|
| `MINUTE_1` | `minute` | 1 minute |
| `MINUTE_3` | `3minute` | 3 minutes |
| `MINUTE_5` | `5minute` | 5 minutes |
| `MINUTE_10` | `10minute` | 10 minutes |
| `MINUTE_15` | `15minute` | 15 minutes |
| `MINUTE_30` | `30minute` | 30 minutes |
| `MINUTE_60` | `60minute` | 60 minutes |
| `DAY_1` | `day` | 1 trading day |

### 5.2 Timeframe rules

| Rule ID | Statement |
|---|---|
| TF-HD-001 | Only the table above is accepted in v1.0. |
| TF-HD-002 | `to_kite_interval(tf)` is the sole mapping to broker wire values. |
| TF-HD-003 | Intraday gap grids use exchange session hours; daily gaps use trading-day calendar. |
| TF-HD-004 | Incomplete in-progress bar at "now" is excluded unless `include_partial_bar=True`. |

---

## 6. Architecture

### 6.1 Component diagram

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                    HistoricalDataService (facade)                        │
│  fetch_range / fetch_latest_n / fetch_session / fetch_rolling_window     │
│  get_cached_series / invalidate_cache / get_health / get_statistics          │
└───────────┬────────────────────────────┬─────────────────────────────────┘
            │                            │
            ▼                            ▼
┌─────────────────────────┐   ┌────────────────────────────────────────────┐
│ HistoricalDataClient    │   │ InstrumentTokenResolver (optional)         │
│ (protocol; injected)    │   │ symbol/exchange → instrument_token         │
└─────────────────────────┘   └────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────┐
│ CandleNormalizer        │
│ broker row → candle     │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ CandleValidator         │
│ VAL-HD-*                │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ GapDetector             │
│ GAP-HD-*                │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ CorporateActionPolicy   │
│ CA-HD-* (optional)      │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ SeriesBuilder           │
│ seal HistoricalSeries   │
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│ HistoricalCache         │
│ memory + disk tiers     │
└─────────────────────────┘
```

### 6.2 Design principles

| Principle | Meaning |
|---|---|
| **Single authority** | One module owns historical OHLCV validation/caching for the platform. |
| **Immutability after seal** | Published series never mutate. |
| **Config-driven** | Underlyings, timeframes, TTLs arrive via `HistoricalDataConfig`. |
| **No hardcoded tokens** | Tokens from caller or resolver. |
| **Fail closed** | Invalid config / naive timestamps / empty required series → typed error. |
| **Deterministic** | Stable sort; stable duplicate winners; stable gap classification. |
| **Thread-safe reads** | Cache swaps are atomic; readers observe sealed series. |
| **Transport isolation** | Broker SDK never imported directly. |
| **No analytics leakage** | No indicator/regime/strategy math. |

### 6.3 Collaborative public types

| Type | Role |
|---|---|
| `HistoricalCandle` | One immutable OHLCV(+OI) bar. |
| `HistoricalSeries` | Sealed ordered candle tuple + quality/provenance. |
| `HistoricalCache` | Memory/disk cache coordinator (facade-visible type). |
| `HistoricalHealth` | Health snapshot. |
| `HistoricalStatistics` | Fetch/cache counters and latency. |
| `HistoricalDataConfig` | Frozen configuration. |
| `HistoricalDataService` | Lifecycle facade. |

**Rule ARCH-HD-001:** Downstream engines never call `kite_broker.fetch_historical` directly once this module is wired.

**Rule ARCH-HD-002:** At most one in-flight broker fetch per cache key unless `allow_concurrent_fetches=True` (default False — single-flight coalesce).

---

## 7. Dependency Direction

```text
ApplicationConfiguration → HistoricalDataConfig
BrokerSession (optional) → HistoricalDataClient adapter → HistoricalDataService
InstrumentLoader / resolver (optional) → token resolution
HistoricalDataService → HistoricalSeries (immutable)
HistoricalSeries → Regime / Indicator / StrategyEval / APME / Dashboard / Paper / Backtest
HistoricalDataService → SystemOrchestrator (health/stats)
```

Forbidden reverse dependencies: this module must not import indicator/strategy/risk/order engines; those engines must not reimplement candle validation/caching.

---

## 8. Configuration — ApplicationConfiguration Projection

### 8.1 `HistoricalDataConfig`

Frozen dataclass validated in `__post_init__`.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled_underlyings` | `tuple[str, ...]` | required | Canonical underlyings allowlist for tagging/validation. |
| `enabled_timeframes` | `tuple[CandleTimeframe, ...]` | all v1.0 TFs | Allowed timeframes. |
| `default_timeframe` | `CandleTimeframe` | `MINUTE_5` | Default when omitted. |
| `session_timezone` | `str` | `"Asia/Kolkata"` | Session calendar zone. |
| `session_open` | `str` | `"09:15"` | Intraday session open (HH:MM local). |
| `session_close` | `str` | `"15:30"` | Intraday session close (HH:MM local). |
| `include_partial_bar` | `bool` | `False` | Include in-progress bar. |
| `strict_validation` | `bool` | `False` | Abort on first hard candle error. |
| `duplicate_policy` | `str` | `"KEEP_LAST_STABLE"` | Duplicate timestamp policy. |
| `missing_candle_policy` | `str` | `"MARK_GAP"` | `MARK_GAP` / `FAIL` / `FORWARD_FILL` (forward-fill Development/Paper only). |
| `max_gap_ratio` | `float` | `0.05` | Max missing/expected before PARTIAL→invalid under policy. |
| `require_non_empty_series` | `bool` | `True` | Fail if zero candles retained. |
| `max_candles_per_request` | `int` | `20000` | Hard safety cap. |
| `memory_cache_enabled` | `bool` | `True` | In-process cache. |
| `memory_cache_max_entries` | `int` | `256` | Max series entries in memory. |
| `memory_cache_ttl_seconds` | `float` | `60.0` | Soft TTL for intraday keys. |
| `disk_cache_enabled` | `bool` | `True` | On-disk cache. |
| `disk_cache_directory` | `str \| None` | `None` | Required in PAPER/PRODUCTION when disk enabled. |
| `disk_cache_ttl_seconds` | `float` | `86400.0` | Disk freshness. |
| `prefer_cache_before_fetch` | `bool` | `True` | Try caches before broker. |
| `continuous` | `bool` | `False` | Passed to broker continuous flag (futures). |
| `corporate_action_policy` | `str` | `"ATTACH_METADATA_ONLY"` | See §15. |
| `allow_equity_fo` | `bool` | `False` | Future equity path. |
| `enabled_equity_underlyings` | `tuple[str, ...]` | `()` | Equity allowlist. |
| `allow_experimental_underlyings` | `bool` | `False` | Experimental names. |
| `publish_events` | `bool` | `False` | Event Bus publish. |
| `environment_profile` | `EnvironmentProfile` | `DEVELOPMENT` | Profile tag. |
| `runner_kind` | `str` | `"unknown"` | Audit tag. |
| `metadata` | `Mapping[str, str]` | `{}` | Non-secret tags. |
| `allow_concurrent_fetches` | `bool` | `False` | Disable single-flight coalesce. |
| `broker_chunk_days` | `int` | `60` | Split large ranges into broker-safe chunks. |

### 8.2 Configuration validation

| Rule ID | Error code |
|---|---|
| CFG-HD-001 empty underlyings | `HD.CONFIG.UNDERLYING_REQUIRED` |
| CFG-HD-002 duplicate underlyings | `HD.CONFIG.UNDERLYING_DUPLICATE` |
| CFG-HD-003 unsupported underlying | `HD.CONFIG.UNDERLYING_UNSUPPORTED` |
| CFG-HD-004 empty/invalid timeframes | `HD.CONFIG.TIMEFRAME_INVALID` |
| CFG-HD-005 bad session times | `HD.CONFIG.SESSION_INVALID` |
| CFG-HD-006 thresholds out of range | `HD.CONFIG.THRESHOLD_OUT_OF_RANGE` |
| CFG-HD-007 invalid policies | `HD.CONFIG.POLICY_INVALID` |
| CFG-HD-008 disk cache path required in PAPER/PRODUCTION | `HD.CONFIG.CACHE_PATH_REQUIRED` |
| CFG-HD-009 FORWARD_FILL forbidden in PRODUCTION | `HD.CONFIG.POLICY_INVALID` |

### 8.3 Helper

```python
def default_historical_data_config(
    profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT,
    *,
    enabled_underlyings: Sequence[str] = ("NIFTY",),
) -> HistoricalDataConfig: ...
```

### 8.4 Profile defaults

| Profile | Notable defaults |
|---|---|
| DEVELOPMENT | experimental allowed; forward-fill permitted; disk optional |
| PAPER | disk required; forward-fill discouraged; experimental off |
| PRODUCTION | disk required; `missing_candle_policy=MARK_GAP` or `FAIL`; forward-fill forbidden; stricter empty-series failure |

---

## 9. Public API

### 9.1 Module constants

| Constant | Value / meaning |
|---|---|
| `HISTORICAL_DATA_VERSION` | `"1.0.0"` |
| `HISTORICAL_DATA_SCHEMA_VERSION` | `"1.0.0"` |
| `PRODUCER_NAME` | `"broker.historical_data"` |
| `SUPPORTED_PRIMARY_UNDERLYINGS` | §4.1 |
| `SUPPORTED_SECONDARY_UNDERLYINGS` | §4.2 |
| `SUPPORTED_INDEX_UNDERLYINGS` | union |
| `TOPIC_SERIES_FETCHED` | `"market.historical.series.fetched"` |
| `TOPIC_SERIES_FAILED` | `"market.historical.series.failed"` |
| `TOPIC_CACHE_UPDATED` | `"market.historical.cache.updated"` |

### 9.2 Enumerations

#### `CandleTimeframe`
See §5.1.

#### `UnderlyingSupportTier`
`PRIMARY`, `SECONDARY`, `EQUITY_FO`, `EXPERIMENTAL`

#### `HistoricalLifecycleState`
`CREATED`, `READY`, `DEGRADED`, `CLOSED`

#### `HistoricalHealthStatus`
`HEALTHY`, `DEGRADED`, `UNHEALTHY`, `UNKNOWN`

#### `SeriesValidationStatus`
`VALID`, `PARTIAL`, `INVALID`

#### `GapSeverity`
`NONE`, `MINOR`, `MAJOR`, `CRITICAL`

#### `MissingCandlePolicy`
`MARK_GAP`, `FAIL`, `FORWARD_FILL`

#### `DuplicatePolicy`
`KEEP_FIRST_STABLE`, `KEEP_LAST_STABLE`, `REJECT`

#### `CorporateActionPolicy`
`ATTACH_METADATA_ONLY`, `APPLY_ADJUSTMENTS`, `REJECT_IF_UNADJUSTED`

#### `CandleSourceKind`
`BROKER_FETCH`, `MEMORY_CACHE`, `DISK_CACHE`, `IN_MEMORY_ROWS`, `MERGED`

### 9.3 Exceptions

| Exception | Prefix |
|---|---|
| `HistoricalDataConfigurationError` | `HD.CONFIG.*` |
| `HistoricalDataStateError` | `HD.STATE.*` |
| `HistoricalDataValidationError` | `HD.VALIDATION.*` |
| `HistoricalDataIOError` | `HD.IO.*` |
| `HistoricalDataSerializationError` | `HD.SERIALIZATION.*` |
| `HistoricalDataLookupError` | `HD.LOOKUP.*` |

Common attributes: `message`, `code`, optional `field`, `underlying`, `instrument_token`, `timeframe`.

### 9.4 Output models

#### 9.4.1 `HistoricalCandle` (frozen)

| Field | Type | Description |
|---|---|---|
| `timestamp` | `datetime` | Bar open time, timezone-aware UTC storage (or UTC-normalized). |
| `open` | `float` | Open |
| `high` | `float` | High |
| `low` | `float` | Low |
| `close` | `float` | Close |
| `volume` | `int` | Volume (`>= 0`) |
| `oi` | `int \| None` | Open interest when present |
| `instrument_token` | `int` | Token |
| `timeframe` | `CandleTimeframe` | Timeframe |
| `exchange_timestamp` | `datetime \| None` | Broker-reported timestamp if distinct |
| `is_adjusted` | `bool` | Corporate-action adjusted flag |
| `gap_after` | `bool` | True if a gap was detected after this bar |
| `metadata` | `Mapping[str, str]` | Non-secret tags |

**Rule MODEL-HD-001:** `high >= max(open, close)` and `low <= min(open, close)` and `high >= low` after validation.

#### 9.4.2 `HistoricalSeries` (frozen)

| Field | Type | Description |
|---|---|---|
| `series_id` | `str` | Seal ID |
| `schema_version` | `str` | Schema version |
| `instrument_token` | `int` | Token |
| `tradingsymbol` | `str \| None` | Optional symbol |
| `underlying` | `str \| None` | Optional canonical underlying |
| `exchange` | `str \| None` | Optional exchange |
| `timeframe` | `CandleTimeframe` | Timeframe |
| `candles` | `tuple[HistoricalCandle, ...]` | Ascending |
| `candle_count` | `int` | `len(candles)` |
| `start` | `datetime \| None` | First timestamp |
| `end` | `datetime \| None` | Last timestamp |
| `validation_status` | `SeriesValidationStatus` | Quality |
| `gap_count` | `int` | Detected gaps |
| `gap_severity` | `GapSeverity` | Worst gap class |
| `missing_bar_count` | `int` | Missing expected bars |
| `duplicate_count` | `int` | Collapsed duplicates |
| `source_kind` | `CandleSourceKind` | Provenance |
| `fetched_at` | `datetime` | Seal time |
| `as_of` | `datetime` | Decision clock |
| `warnings` | `tuple[str, ...]` | Soft issues |
| `errors` | `tuple[str, ...]` | Hard issues (usually empty if sealed) |
| `metadata` | `Mapping[str, str]` | Non-secret tags |

Ordering: strictly ascending by `timestamp`; ties forbidden after duplicate policy.

#### 9.4.3 `HistoricalCache` (public collaborative component)

Not a frozen dataclass of all entries; a thread-safe component exposing:

- `get(key) -> HistoricalSeries | None`
- `put(key, series) -> None`
- `invalidate(key | None) -> None`
- `stats() -> Mapping[str, int]`

Backed by memory map + optional disk store.

#### 9.4.4 `HistoricalHealth` (frozen)

| Field | Type |
|---|---|
| `report_id` | `str` |
| `as_of` | `datetime` |
| `lifecycle_state` | `HistoricalLifecycleState` |
| `overall_health` | `HistoricalHealthStatus` |
| `memory_cache_entries` | `int` |
| `disk_cache_enabled` | `bool` |
| `disk_cache_healthy` | `bool` |
| `last_fetch_latency_ms` | `float \| None` |
| `error_rate` | `float` |
| `issues` | `tuple[HistoricalHealthIssue, ...]` |
| `statistics` | `HistoricalStatistics` |
| `metadata` | `Mapping[str, str]` |

#### 9.4.5 `HistoricalHealthIssue` (frozen)

`issue_code`, `severity` (`info|warning|error`), `message`, optional `underlying`, `instrument_token`, `timeframe`.

#### 9.4.6 `HistoricalStatistics` (frozen)

| Field | Type | Description |
|---|---|---|
| `as_of` | `datetime` | |
| `total_fetch_count` | `int` | Broker fetches attempted |
| `total_fetch_success_count` | `int` | |
| `total_fetch_error_count` | `int` | |
| `memory_cache_hit_count` | `int` | |
| `memory_cache_miss_count` | `int` | |
| `disk_cache_hit_count` | `int` | |
| `disk_cache_miss_count` | `int` | |
| `series_sealed_count` | `int` | |
| `validation_reject_count` | `int` | |
| `gap_detection_count` | `int` | |
| `average_fetch_latency_ms` | `float \| None` | |
| `max_fetch_latency_ms` | `float \| None` | |
| `error_rate` | `float` | errors / attempts |
| `candles_served_count` | `int` | |
| `last_error_code` | `str \| None` | |
| `per_timeframe` | `Mapping[str, int]` | sealed series counts |

### 9.5 Protocols

```python
class HistoricalDataClient(Protocol):
    def fetch_historical_rows(
        self,
        *,
        instrument_token: int,
        from_ts: datetime,
        to_ts: datetime,
        interval: str,
        continuous: bool = False,
    ) -> Sequence[Mapping[str, Any]]:
        """Return raw broker candle mappings."""

class InstrumentTokenResolver(Protocol):
    def resolve_token(
        self,
        *,
        exchange: str,
        tradingsymbol: str,
    ) -> int:
        """Resolve trading symbol to instrument token."""
```

**Rule API-HD-001:** The service never constructs `KiteConnect` itself.

### 9.6 `HistoricalDataService` facade

```python
class HistoricalDataService:
    def __init__(
        self,
        config: HistoricalDataConfig,
        *,
        client: HistoricalDataClient | None = None,
        resolver: InstrumentTokenResolver | None = None,
        event_bus: EventBus | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None: ...

    def get_status(self) -> HistoricalLifecycleState: ...
    def close(self) -> None: ...

    def fetch_range(
        self,
        *,
        instrument_token: int | None = None,
        exchange: str | None = None,
        tradingsymbol: str | None = None,
        timeframe: CandleTimeframe | None = None,
        from_ts: datetime,
        to_ts: datetime,
        underlying: str | None = None,
        continuous: bool | None = None,
    ) -> HistoricalSeries: ...

    def fetch_latest_n(
        self,
        *,
        instrument_token: int | None = None,
        exchange: str | None = None,
        tradingsymbol: str | None = None,
        timeframe: CandleTimeframe | None = None,
        n: int,
        underlying: str | None = None,
        as_of: datetime | None = None,
    ) -> HistoricalSeries: ...

    def fetch_session(
        self,
        *,
        instrument_token: int | None = None,
        exchange: str | None = None,
        tradingsymbol: str | None = None,
        timeframe: CandleTimeframe | None = None,
        session_date: date,
        underlying: str | None = None,
    ) -> HistoricalSeries: ...

    def fetch_rolling_window(
        self,
        *,
        instrument_token: int | None = None,
        exchange: str | None = None,
        tradingsymbol: str | None = None,
        timeframe: CandleTimeframe | None = None,
        window: timedelta,
        as_of: datetime | None = None,
        underlying: str | None = None,
    ) -> HistoricalSeries: ...

    def load_from_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        instrument_token: int,
        timeframe: CandleTimeframe,
        underlying: str | None = None,
        tradingsymbol: str | None = None,
    ) -> HistoricalSeries: ...

    def get_cached_series(self, cache_key: str) -> HistoricalSeries | None: ...
    def invalidate_cache(self, cache_key: str | None = None) -> None: ...
    def get_health(self) -> HistoricalHealth: ...
    def get_statistics(self) -> HistoricalStatistics: ...
    def reset_statistics(self) -> None: ...
```

**Rule API-HD-002:** Exactly one of `instrument_token` or (`exchange` + `tradingsymbol`) must be provided for fetch APIs.

**Rule API-HD-003:** All public timestamps must be timezone-aware; naive values raise `HD.VALIDATION.NAIVE_TIMESTAMP`.

---

## 10. Fetch Pipeline

### 10.1 Identity resolution

1. If `instrument_token` provided: validate `> 0`.
2. Else require `exchange` + `tradingsymbol` and call `resolver.resolve_token(...)`.
3. Missing resolver → `HD.STATE.RESOLVER_NOT_CONFIGURED`.
4. Optional `underlying` is validated against enabled catalog when provided.

### 10.2 Cache lookup order

When `prefer_cache_before_fetch=True`:

1. Memory cache by canonical key.
2. Disk cache (if enabled and fresh).
3. Broker fetch (chunked if needed).
4. Seal series → write memory → optional disk.

Canonical cache key:

```text
hd:{token}:{timeframe}:{from_iso}:{to_iso}:c={continuous}:p={include_partial_bar}
```

For `latest_n` / rolling / session, normalize to an explicit `[from,to]` before keying.

### 10.3 Broker chunking

If `(to_ts - from_ts).days > broker_chunk_days`, split into contiguous chunks, fetch sequentially (deterministic order), merge, then validate once.

### 10.4 Single-flight coalescing

Concurrent identical cache-key fetches share one in-flight future unless `allow_concurrent_fetches=True`.

### 10.5 Raw row mapping

Expected broker fields (Zerodha-style):

| Broker field | Candle field |
|---|---|
| `date` / `timestamp` | `timestamp` / `exchange_timestamp` |
| `open` | `open` |
| `high` | `high` |
| `low` | `low` |
| `close` | `close` |
| `volume` | `volume` |
| `oi` | `oi` |

---

## 11. Timestamp Normalization

| Rule ID | Statement |
|---|---|
| TZ-HD-001 | Public API rejects naive `from_ts` / `to_ts` / `as_of`. |
| TZ-HD-002 | Broker naive timestamps are interpreted as `session_timezone` then stored UTC. |
| TZ-HD-003 | Aware non-UTC timestamps convert to UTC for storage/comparison. |
| TZ-HD-004 | Series `start`/`end`/`fetched_at`/`as_of` are timezone-aware. |
| TZ-HD-005 | Session boundaries computed in `session_timezone`. |

```python
def normalize_candle_timestamp(
    value: datetime,
    *,
    assume_tz: str = "Asia/Kolkata",
) -> datetime:
    """Return timezone-aware UTC datetime."""
```

---

## 12. Validation

### 12.1 Per-candle rules (VAL-HD-*)

| Rule ID | Condition | Code | Default disposition |
|---|---|---|---|
| VAL-HD-001 | timestamp timezone-aware after normalize | `HD.VALIDATION.NAIVE_TIMESTAMP` | reject row / fail strict |
| VAL-HD-002 | open/high/low/close finite and `> 0` | `HD.VALIDATION.INVALID_PRICE` | discard |
| VAL-HD-003 | `high >= low` | `HD.VALIDATION.OHLC_INCONSISTENT` | discard |
| VAL-HD-004 | `high >= max(open, close)` and `low <= min(open, close)` | `HD.VALIDATION.OHLC_INCONSISTENT` | discard |
| VAL-HD-005 | `volume >= 0` | `HD.VALIDATION.INVALID_VOLUME` | discard |
| VAL-HD-006 | `oi is None or oi >= 0` | `HD.VALIDATION.INVALID_OI` | discard |
| VAL-HD-007 | `instrument_token > 0` | `HD.VALIDATION.INVALID_TOKEN` | fail request |

### 12.2 Series-level rules

| Rule ID | Condition | Code |
|---|---|---|
| VAL-HD-010 | timestamps strictly increasing after dedupe | `HD.VALIDATION.TIMESTAMP_ORDER` |
| VAL-HD-011 | duplicates handled per policy | `HD.VALIDATION.DUPLICATE_CANDLE` |
| VAL-HD-012 | empty series + require_non_empty | `HD.VALIDATION.EMPTY_SERIES` |
| VAL-HD-013 | candle_count ≤ max_candles_per_request | `HD.VALIDATION.TOO_MANY_CANDLES` |
| VAL-HD-014 | from_ts ≤ to_ts | `HD.VALIDATION.INVALID_RANGE` |

### 12.3 Duplicate policy

| Policy | Behaviour |
|---|---|
| `KEEP_FIRST_STABLE` | Keep earliest row in input order for a timestamp |
| `KEEP_LAST_STABLE` | Keep latest row in input order |
| `REJECT` | Abort seal with `HD.VALIDATION.DUPLICATE_CANDLE` |

---

## 13. Gap Detection and Missing Candles

### 13.1 Expected grid

For intraday timeframes, build expected bar opens from `session_open` to `session_close` stepping by timeframe duration, for each trading day intersecting `[from,to]`.

For `DAY_1`, expected grid is trading days in range (weekends/holidays excluded when a holiday calendar is injected; if no calendar, weekends-only exclusion with warning `HD.HEALTH.HOLIDAY_CALENDAR_MISSING`).

### 13.2 Gap classification

| Missing ratio | Severity |
|---|---|
| 0 | `NONE` |
| ≤ `max_gap_ratio` | `MINOR` |
| ≤ 3 × `max_gap_ratio` | `MAJOR` |
| > 3 × `max_gap_ratio` | `CRITICAL` |

### 13.3 Missing-candle policy

| Policy | Behaviour |
|---|---|
| `MARK_GAP` | Do not invent bars; set `gap_after` on preceding candle; series may be `PARTIAL` |
| `FAIL` | Abort with `HD.VALIDATION.MISSING_CANDLES` when any gap |
| `FORWARD_FILL` | Synthesize bars using previous close (OHLC=prev close, volume=0); mark metadata `synthetic=true`; **forbidden in PRODUCTION** |

**Rule GAP-HD-001:** Production never silently fabricates prices.

**Rule GAP-HD-002:** Gap detection runs after duplicate collapse and sort.

---

## 14. Query Semantics

### 14.1 Date range

Inclusive of bars with `timestamp >= from_ts` and `timestamp <= to_ts` after normalization. Partial bar at `to_ts≈now` excluded unless configured.

### 14.2 Latest N

Compute `to = as_of or clock()`, fetch a lookback sufficient for N bars (config heuristic: `n * timeframe + session slack`), seal, return last N.

### 14.3 Session

`session_date` in `session_timezone`: `[session_open, session_close]` that day. Daily timeframe returns at most one bar for that date if present.

### 14.4 Rolling window

`from = as_of - window`, `to = as_of`, then date-range semantics.

---

## 15. Corporate Action Awareness

v1.0 supports **awareness**, not a full corporate-actions vendor integration.

| Policy | Behaviour |
|---|---|
| `ATTACH_METADATA_ONLY` | Pass through broker continuous/adjusted flags into candle/series metadata; do not recompute prices |
| `APPLY_ADJUSTMENTS` | Apply injected adjustment factors table if provided; else fail `HD.CA.FACTORS_REQUIRED` |
| `REJECT_IF_UNADJUSTED` | Fail when series spans known CA event without adjustment metadata |

**Rule CA-HD-001:** This module never scrapes exchange notices.

**Rule CA-HD-002:** Adjustment factor tables are injected; never hardcoded per symbol.

---

## 16. Caching

### 16.1 Memory cache

- Key → sealed `HistoricalSeries`
- LRU eviction by `memory_cache_max_entries`
- TTL: `memory_cache_ttl_seconds` (intraday); daily keys may use longer effective TTL = max(ttl, 300s)

### 16.2 Disk cache

Versioned JSON documents:

```json
{
  "schema_version": "1.0.0",
  "cache_key": "hd:...",
  "checksum": "sha256:...",
  "written_at": "2026-08-05T10:00:00Z",
  "series": { "...HistoricalSeries..." }
}
```

Rules:

| Rule ID | Statement |
|---|---|
| CACHE-HD-001 | Atomic write (temp + replace). |
| CACHE-HD-002 | Checksum covers series payload. |
| CACHE-HD-003 | Stale disk → miss (unless `allow_stale_disk_cache`). |
| CACHE-HD-004 | Corrupt disk → `HD.IO.CACHE_CORRUPT` then fallback to fetch. |
| CACHE-HD-005 | No secrets in cache files. |

### 16.3 Refresh policy

| Condition | Action |
|---|---|
| Complete historical range ending before previous session close | Cache indefinitely until TTL |
| Range ending at/after current session | Short TTL; prefer refresh |
| Explicit `invalidate_cache` | Drop memory (+ optional disk) |

### 16.4 `HistoricalCache` concurrency

- Sharded or single `RLock` around map mutations
- Readers copy series references (immutable)
- Single-flight map for in-progress fetches

---

## 17. Performance

### 17.1 Budgets

| Operation | Budget |
|---|---|
| Memory cache hit | ≤ 50 µs |
| Seal 5k candles (validate+gap) | ≤ 100 ms |
| Disk cache read 5k candles | ≤ 50 ms |
| Broker fetch round-trip | dominated by network (measured, not budgeted as CPU) |
| Concurrent 32 readers on cached series | 0 errors; p99 < 200 µs |

### 17.2 Rules

| Rule ID | Statement |
|---|---|
| PERF-HD-001 | Hot path must not re-fetch when memory fresh. |
| PERF-HD-002 | No indicator math on fetch path. |
| PERF-HD-003 | Chunk merges allocate once per seal. |
| PERF-HD-004 | Statistics updates are O(1). |

---

## 18. Health Reporting

### 18.1 Status derivation

| Condition | Status |
|---|---|
| READY + error_rate < 0.05 + disk healthy (if enabled) | HEALTHY |
| Elevated error_rate or disk degraded or MAJOR gaps frequent | DEGRADED |
| No successful fetch ever + recent failures / CLOSED without cache | UNHEALTHY |
| CREATED | UNKNOWN |

### 18.2 Issue codes

| Code | Severity | Meaning |
|---|---|---|
| `HD.HEALTH.NO_CLIENT` | error | Broker client missing when fetch required |
| `HD.HEALTH.DISK_CACHE_UNAVAILABLE` | warning/error | Disk path missing/unwritable |
| `HD.HEALTH.HIGH_ERROR_RATE` | warning/error | error_rate above threshold |
| `HD.HEALTH.HIGH_LATENCY` | warning | avg latency above threshold |
| `HD.HEALTH.HOLIDAY_CALENDAR_MISSING` | info | weekends-only gap grid |
| `HD.HEALTH.STALE_INTRADAY_CACHE` | warning | serving near-TTL intraday data |

---

## 19. Statistics

`get_statistics()` returns frozen counters since last `reset_statistics()` (or process start).

Error rate = `total_fetch_error_count / max(1, total_fetch_count)`.

Latency uses exponential or arithmetic mean documented as arithmetic mean of successful broker fetches.

---

## 20. Error Codes

### 20.1 Config / state

| Code | Meaning |
|---|---|
| `HD.CONFIG.UNDERLYING_REQUIRED` | Empty underlyings |
| `HD.CONFIG.UNDERLYING_DUPLICATE` | Duplicate underlying |
| `HD.CONFIG.UNDERLYING_UNSUPPORTED` | Not allowlisted |
| `HD.CONFIG.TIMEFRAME_INVALID` | Bad timeframe |
| `HD.CONFIG.SESSION_INVALID` | Bad session open/close |
| `HD.CONFIG.THRESHOLD_OUT_OF_RANGE` | Numeric threshold invalid |
| `HD.CONFIG.POLICY_INVALID` | Bad policy |
| `HD.CONFIG.CACHE_PATH_REQUIRED` | Disk directory missing |
| `HD.STATE.NOT_READY` | Service not ready |
| `HD.STATE.CLOSED` | Closed |
| `HD.STATE.CLIENT_NOT_CONFIGURED` | No broker client |
| `HD.STATE.RESOLVER_NOT_CONFIGURED` | Symbol used without resolver |
| `HD.STATE.FETCH_IN_PROGRESS` | Optional conflict when coalescing disabled |

### 20.2 Validation / IO / lookup / CA / serialization

| Code | Meaning |
|---|---|
| `HD.VALIDATION.NAIVE_TIMESTAMP` | Naive datetime |
| `HD.VALIDATION.INVALID_PRICE` | Non-finite/non-positive OHLC |
| `HD.VALIDATION.OHLC_INCONSISTENT` | OHLC relationship violated |
| `HD.VALIDATION.INVALID_VOLUME` | Negative volume |
| `HD.VALIDATION.INVALID_OI` | Negative OI |
| `HD.VALIDATION.INVALID_TOKEN` | Token ≤ 0 |
| `HD.VALIDATION.TIMESTAMP_ORDER` | Non-ascending series |
| `HD.VALIDATION.DUPLICATE_CANDLE` | Duplicate policy reject |
| `HD.VALIDATION.EMPTY_SERIES` | No candles |
| `HD.VALIDATION.TOO_MANY_CANDLES` | Exceeds cap |
| `HD.VALIDATION.INVALID_RANGE` | from > to |
| `HD.VALIDATION.MISSING_CANDLES` | FAIL policy on gaps |
| `HD.IO.BROKER_FETCH_FAILED` | Transport failure |
| `HD.IO.CACHE_CORRUPT` | Disk checksum/schema failure |
| `HD.IO.CACHE_STALE` | Disk older than TTL |
| `HD.IO.FILE_NOT_FOUND` | Missing cache file |
| `HD.IO.WRITE_FAILED` | Disk write failure |
| `HD.LOOKUP.TOKEN_REQUIRED` | Identity missing |
| `HD.LOOKUP.SYMBOL_UNRESOLVED` | Resolver miss |
| `HD.CA.FACTORS_REQUIRED` | Adjustments requested without factors |
| `HD.CA.UNADJUSTED_SPAN` | Reject policy triggered |
| `HD.SERIALIZATION.MALFORMED` | Bad payload |
| `HD.SERIALIZATION.UNSUPPORTED_VERSION` | Schema major mismatch |

---

## 21. Security

| Rule ID | Statement |
|---|---|
| SEC-HD-001 | Cache files must not contain access tokens. |
| SEC-HD-002 | Metadata maps are non-secret. |
| SEC-HD-003 | Broker errors logged without auth headers. |
| SEC-HD-004 | Path operations never use shell=True. |
| SEC-HD-005 | No hardcoded production credentials. |

---

## 22. Thread Safety and Determinism

| Rule ID | Statement |
|---|---|
| THR-HD-001 | Cache mutations under lock; sealed series immutable. |
| THR-HD-002 | Single-flight coalesce per cache key by default. |
| THR-HD-003 | Statistics updates atomic / locked. |
| DET-HD-001 | Identical raw rows + config + clock + id_factory → identical series payload (modulo series_id if id_factory not fixed). |
| DET-HD-002 | Gap grids are pure functions of session config + timeframe + holiday calendar. |

---

## 23. Serialization

Public serializers:

- `serialize_historical_candle` / `deserialize_historical_candle`
- `serialize_historical_series` / `deserialize_historical_series`
- `serialize_historical_health` / `deserialize_historical_health`
- `serialize_historical_statistics` / `deserialize_historical_statistics`
- `*_to_json` / `*_from_json`

Rules:

| Rule ID | Statement |
|---|---|
| SER-HD-001 | `schema_version` on top-level documents |
| SER-HD-002 | Datetimes ISO-8601 UTC with `Z` |
| SER-HD-003 | Enums by value |
| SER-HD-004 | Unknown major → `HD.SERIALIZATION.UNSUPPORTED_VERSION` |
| SER-HD-005 | Deserialization re-validates OHLC invariants |

---

## 24. Lifecycle / State Machine

```text
CREATED → READY (successful first operation / explicit start)
READY → DEGRADED (elevated errors / disk failure while serving cache)
DEGRADED → READY (recovery)
* → CLOSED (terminal)
```

`close()` prevents new fetches; cached reads may still be allowed until process exit if `allow_reads_after_close=False` (default: raise `HD.STATE.CLOSED`).

---

## 25. Event Bus Topics

| Topic | When |
|---|---|
| `market.historical.series.fetched` | Successful seal from broker or cache miss fill |
| `market.historical.series.failed` | Fetch/validation failure |
| `market.historical.cache.updated` | Memory/disk put |

Publish only when `publish_events=True` and bus injected. Publish failures isolated.

---

## 26. Integration with Consumers

### 26.1 Market Regime Engine

```python
series = service.fetch_rolling_window(
    instrument_token=token,
    timeframe=CandleTimeframe.MINUTE_5,
    window=timedelta(days=5),
)
# engine consumes series.candles closes/volumes — never fetches broker itself
```

### 26.2 Indicator Engine

Pulls `HistoricalSeries` and computes indicators locally.

### 26.3 Strategy Evaluation / Backtesting

Uses `fetch_range` for deterministic windows; must inject fixed `clock` + fixture rows for unit tests.

### 26.4 APME / Dashboard / Paper

Read-only latest-N / session queries; never mutate cache except via service APIs.

### 26.5 System Orchestrator

Constructs service, injects client/resolver, aggregates `get_health()`.

### 26.6 Relationship to market_data_engine R12

Raw optional historical passthrough in Market Data Engine remains legacy-compatible but **platform analytical consumers must use this module** for validated series.

---

## 27. Testing Requirements

### 27.1 Unit test file

`tests/test_historical_data.py` — required.

Target: **≥ 95%** coverage of `broker/historical_data.py`.

### 27.2 Mandatory categories

| Category | Examples |
|---|---|
| Config validation | underlyings, timeframes, session, FORWARD_FILL in PRODUCTION |
| Normalization | naive broker ts → UTC; aware conversion |
| Validation | OHLC inconsistent, negative volume, naive public ts |
| Duplicates | KEEP_FIRST / KEEP_LAST / REJECT |
| Ordering | ascending guarantee |
| Gap detection | missing intraday bars; severity classes |
| Missing policy | MARK_GAP / FAIL / FORWARD_FILL |
| Fetch APIs | range, latest N, session, rolling window |
| Token vs symbol | resolver path + missing resolver |
| Caching | memory hit/miss, disk round-trip, stale, corrupt |
| Chunking | multi-chunk merge |
| Single-flight | concurrent identical fetches |
| Serialization | round-trip + malformed + unsupported version |
| Concurrency | readers during cache refresh |
| Determinism | identical rows → identical series |
| Boundaries | no kiteconnect/KiteTicker/place_order/indicator imports; no hardcoded tokens |
| Catalog parity | primary/secondary frozensets match peer modules |
| Performance | smoke benchmarks under marker |

### 27.3 Fixtures

- Synthetic candle rows under `tests/fixtures/historical/`
- Fake `HistoricalDataClient`
- Fixed clock `datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)`
- No live broker calls in unit tests

### 27.4 Contract tests

| Test | Assertion |
|---|---|
| `test_underlying_catalog_parity` | matches websocket/streaming/instrument_loader |
| `test_timeframe_kite_interval_map` | every enum maps to documented kite interval |
| `test_no_hardcoded_instrument_tokens` | grep compliance |
| `test_no_indicator_logic` | no EMA/RSI/SMA formula constants in module |

### 27.5 Static compliance grep

CI fails if module contains: `kiteconnect`, `KiteTicker`, `place_order`, `generate_session`, obvious indicator kernels (`def ema(`, `def rsi(`), hardcoded sole-path `"NSE:NIFTY 50"` as only identity source.

---

## 28. Implementation Checklist

1. Create `broker/historical_data.py` with constants, enums, frozen models, exceptions.
2. Implement `HistoricalDataConfig` validation + `default_historical_data_config`.
3. Implement timeframe ↔ kite interval map.
4. Implement timestamp normalization helpers.
5. Implement `CandleValidator` and duplicate collapse.
6. Implement `GapDetector` with session grid.
7. Implement corporate-action policy hooks.
8. Implement `SeriesBuilder` sealing `HistoricalSeries`.
9. Implement `HistoricalCache` (memory LRU + disk).
10. Implement `HistoricalDataService` fetch APIs + single-flight.
11. Implement chunked broker fetch merge.
12. Implement health + statistics.
13. Implement serializers.
14. Implement optional Event Bus publish.
15. Add `tests/test_historical_data.py` (≥ 95%).
16. Add fixtures + contract/parity tests.
17. Update `CHANGELOG.md` and cross-link from `kite_broker.md` / `market_data_engine.md`.
18. Run static compliance greps in CI.

---

## 29. Definition of Done

This module is done when **all** of the following are true:

1. `broker/historical_data.py` exists as a complete production implementation (no placeholders).
2. `tests/test_historical_data.py` exists with ≥ 95% coverage.
3. Public models `HistoricalCandle`, `HistoricalSeries`, `HistoricalCache`, `HistoricalHealth`, `HistoricalStatistics` are implemented per this specification.
4. Service can fetch via injected client, serve from memory/disk cache, and seal from in-memory rows.
5. Validation covers missing candles, duplicates, timestamp order, OHLC consistency, volume.
6. Gap detection and missing-candle policies behave as documented.
7. Timezone normalization rejects naive public inputs.
8. Corporate-action policy hooks exist (even if ATTACH_METADATA_ONLY is default).
9. Thread-safe caching verified by concurrency tests.
10. Deterministic seal verified by tests.
11. Versioned JSON serialization works.
12. Health exposes cache health; statistics expose latency and error rate.
13. Architecture boundaries enforced: **MUST NOT** stream live ticks, evaluate strategies, calculate indicators, place orders, or execute trades.
14. No hardcoded instrument tokens as sole-path identity.
15. Catalog/timeframe contract tests pass.
16. Documentation cross-links updated.

---

## 30. Non-Goals / Explicit Non-Changes

1. Do not redesign THETA AI TRADER architecture.
2. Do not merge this module into `kite_broker.py` or `market_data_streaming.py`.
3. Do not add indicator/regime/strategy APIs.
4. Do not scrape unofficial candle sources.
5. Do not stream WebSocket ticks.
6. Do not invent PRODUCTION forward-filled prices as default.
7. Do not make Application Configuration fetch candles.
8. Do not hardcode production instrument tokens.
9. Equity F&O remains disabled by default.
10. Holiday calendar injection is optional; absence yields weekends-only exclusion + health info.

---

## Appendix A — Worked Example: Fetch NIFTY 5-Minute Range

```python
from datetime import datetime, timedelta, timezone
from broker.historical_data import (
    HistoricalDataService,
    HistoricalDataConfig,
    CandleTimeframe,
)

FIXED_NOW = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)

config = HistoricalDataConfig(
    enabled_underlyings=("NIFTY", "BANKNIFTY", "SENSEX"),
    default_timeframe=CandleTimeframe.MINUTE_5,
    disk_cache_enabled=False,
    memory_cache_enabled=True,
    missing_candle_policy="MARK_GAP",
    runner_kind="paper",
)
service = HistoricalDataService(
    config,
    client=fake_client,          # injected
    clock=lambda: FIXED_NOW,
    id_factory=lambda: "series-fixed",
)

series = service.fetch_range(
    instrument_token=resolved_nifty_token,  # from instrument_loader — not hardcoded here
    timeframe=CandleTimeframe.MINUTE_5,
    from_ts=FIXED_NOW - timedelta(days=1),
    to_ts=FIXED_NOW,
    underlying="NIFTY",
)
assert series.candle_count > 0
assert series.validation_status.value in {"VALID", "PARTIAL"}
assert series.candles[0].timestamp <= series.candles[-1].timestamp
```

---

## Appendix B — Example Raw Broker Candle Rows (illustrative)

```json
[
  {
    "date": "2026-08-05T09:15:00+05:30",
    "open": 24510.0,
    "high": 24525.5,
    "low": 24505.0,
    "close": 24520.0,
    "volume": 120345,
    "oi": 0
  },
  {
    "date": "2026-08-05T09:20:00+05:30",
    "open": 24520.0,
    "high": 24530.0,
    "low": 24512.0,
    "close": 24518.0,
    "volume": 98670,
    "oi": 0
  }
]
```

Tokens are supplied by the caller/resolver — never embedded as sole-path constants in this module.

---

## Appendix C — Full Seal Pipeline Pseudocode

```python
def seal_series(rows, *, token, timeframe, config, clock, id_factory, source_kind):
    t0 = perf_counter()
    normalized = [normalize_row(r, token, timeframe, config) for r in rows]
    valid, discarded = [], 0
    for candle in normalized:
        issues = validate_candle(candle)
        if issues:
            if config.strict_validation:
                raise HistoricalDataValidationError(...)
            discarded += 1
        else:
            valid.append(candle)
    valid = collapse_duplicates(valid, config.duplicate_policy)
    valid.sort(key=lambda c: c.timestamp)
    ensure_strictly_increasing(valid)
    gaps = detect_gaps(valid, timeframe, config)
    apply_missing_policy(valid, gaps, config)  # may synthesize only if FORWARD_FILL allowed
    status = classify_series_status(valid, gaps, discarded, config)
    if config.require_non_empty_series and not valid:
        raise HistoricalDataValidationError(code="HD.VALIDATION.EMPTY_SERIES")
    if len(valid) > config.max_candles_per_request:
        raise HistoricalDataValidationError(code="HD.VALIDATION.TOO_MANY_CANDLES")
    series = HistoricalSeries(
        series_id=id_factory(),
        schema_version=HISTORICAL_DATA_SCHEMA_VERSION,
        instrument_token=token,
        timeframe=timeframe,
        candles=tuple(valid),
        candle_count=len(valid),
        start=valid[0].timestamp if valid else None,
        end=valid[-1].timestamp if valid else None,
        validation_status=status,
        gap_count=len(gaps),
        gap_severity=worst(gaps),
        missing_bar_count=sum(g.missing for g in gaps),
        duplicate_count=...,
        source_kind=source_kind,
        fetched_at=clock(),
        as_of=clock(),
        ...
    )
    return series
```

---

## Appendix D — Concurrency Sketches

### D.1 Single-flight fetch

```text
key = cache_key(...)
with inflight_lock:
  if key in inflight: future = inflight[key]
  else: future = start_fetch(); inflight[key] = future
series = await_or_result(future)
cache.put(key, series)
```

### D.2 Readers during refresh

Old sealed series remain readable until map replace. No torn candle tuples.

### D.3 Forbidden patterns

- Mutating `series.candles` list in place
- Fetching broker inside indicator loops without cache
- Sharing mutable candle dicts

---

## Appendix E — Glossary

| Term | Definition |
|---|---|
| HistoricalCandle | One immutable OHLCV(+OI) bar |
| HistoricalSeries | Sealed ordered candle tuple with quality |
| Gap | Missing expected bar on the timeframe grid |
| Session | Exchange trading session in `session_timezone` |
| Partial bar | In-progress candle not yet closed |
| Forward fill | Synthetic bar copying previous close |
| Continuous | Broker flag for continuous futures series |
| Cache key | Deterministic identity for a sealed request |

---

## Appendix F — Migration Notes

| Legacy pattern | v1.0 replacement |
|---|---|
| Ad-hoc `kite.historical_data` in scripts | `HistoricalDataService.fetch_*` |
| Market Data Engine raw R12 passthrough for analytics | This module's validated series |
| Per-engine candle caches | Shared `HistoricalCache` |
| Indicator engines fetching broker directly | Inject `HistoricalDataService` |

---

## Appendix G — Performance Benchmark Targets

| Benchmark | Target |
|---|---|
| `bench_seal_5k` | < 100 ms |
| `bench_memory_hit_10k` | < 50 ms total |
| `bench_disk_roundtrip_5k` | < 80 ms |
| `bench_concurrent_reads_32t` | 0 errors |

---

## Appendix H — Non-Goals Confirmation Checklist

- [ ] No WebSocket / tick streaming
- [ ] No indicator calculations
- [ ] No strategy evaluation
- [ ] No order placement / trade execution
- [ ] No hardcoded sole-path instrument tokens
- [ ] No `.env` loading
- [ ] No silent PRODUCTION forward-fill

---

## Appendix I — Failure Scenario Matrix

| Scenario | Result |
|---|---|
| Broker timeout | `HD.IO.BROKER_FETCH_FAILED`; prior cache retained if any |
| OHLC high < low | row discarded or strict fail |
| Duplicate timestamps + REJECT | seal aborted |
| Naive from_ts | `HD.VALIDATION.NAIVE_TIMESTAMP` |
| Symbol without resolver | `HD.STATE.RESOLVER_NOT_CONFIGURED` |
| Disk corrupt | miss + fetch fallback |
| FORWARD_FILL in PRODUCTION config | config construct fails |
| Empty broker response + require_non_empty | `HD.VALIDATION.EMPTY_SERIES` |

---

## Appendix J — Configuration Defaults by Profile

### Development

```text
allow_experimental_underlyings = True
missing_candle_policy = MARK_GAP (FORWARD_FILL allowed if explicitly set)
disk_cache_enabled = False unless directory provided
memory_cache_ttl_seconds = 30
strict_validation = False
publish_events = False
```

### Paper

```text
allow_experimental_underlyings = False
disk_cache_enabled = True
disk_cache_directory required
missing_candle_policy = MARK_GAP
memory_cache_ttl_seconds = 60
publish_events = True
```

### Production

```text
allow_experimental_underlyings = False
disk_cache_enabled = True
disk_cache_directory required
missing_candle_policy = MARK_GAP or FAIL
FORWARD_FILL forbidden
strict_validation = False (soft discard) unless ops enables True
include_partial_bar = False
publish_events = True
```

---

## Appendix K — Session Grid Example (NIFTY 5-minute)

Session `09:15`–`15:30` IST, timeframe 5 minutes.

Expected bar opens include:

```text
09:15, 09:20, 09:25, ..., 15:25
```

Notes:

- `15:30` is session close; final bar open is `15:25` for 5-minute bars.
- If broker omits `09:40`, gap detector marks a missing bar between `09:35` and `09:45`.
- Under `MARK_GAP`, series remains available with `gap_after=True` on `09:35` candle.

---

## Appendix L — Related Documents

- `docs/specifications/kite_broker.md` (§13 Historical Data Retrieval)
- `docs/specifications/kite_authentication.md`
- `docs/specifications/kite_websocket.md`
- `docs/specifications/instrument_loader.md`
- `docs/specifications/market_data_streaming.md`
- `docs/specifications/market_data_engine.md`
- `docs/specifications/application_configuration.md`
- `docs/specifications/system_orchestrator.md`
- `docs/specifications/event_bus.md`
- Market Regime / Indicator / Strategy Evaluation / APME specifications (consumers)

---

## Appendix M — Implementation Checklist (engineer, expanded)

1. Mirror coding standards: type hints, frozen dataclasses, Google docstrings, PEP 8.
2. Keep validators/gap detectors pure and unit-testable without I/O.
3. Implement `to_kite_interval` / `from_kite_interval` as the only wire maps.
4. Ensure cache keys are stable under timezone normalization.
5. Wire `default_historical_data_config(profile)`.
6. Export `__all__` for public API.
7. Avoid circular imports with instrument_loader (protocol-only resolver).
8. Add CHANGELOG entry.
9. Add CI grep compliance job.
10. Verify tests on repository Python version (3.9+).

---

## Appendix N — Performance Tuning Guidance

| Symptom | Likely cause | Tuning lever |
|---|---|---|
| Repeated broker fetches | TTL too low / cache disabled | Raise TTL; enable disk cache |
| High seal latency | gap grid over huge ranges | Narrow range; raise timeframe |
| Memory growth | `memory_cache_max_entries` too high | Lower cap; rely on disk |
| Lock contention | concurrent fetches without coalesce | keep `allow_concurrent_fetches=False` |
| Excessive PARTIAL series | holiday calendar missing | inject holiday calendar |

---

## Appendix O — Example HistoricalHealth JSON (illustrative)

```json
{
  "schema_version": "1.0.0",
  "report_id": "health-1",
  "as_of": "2026-08-05T10:05:00Z",
  "lifecycle_state": "READY",
  "overall_health": "HEALTHY",
  "memory_cache_entries": 12,
  "disk_cache_enabled": true,
  "disk_cache_healthy": true,
  "last_fetch_latency_ms": 182.4,
  "error_rate": 0.0,
  "issues": [],
  "statistics": {
    "schema_version": "1.0.0",
    "as_of": "2026-08-05T10:05:00Z",
    "total_fetch_count": 4,
    "total_fetch_success_count": 4,
    "total_fetch_error_count": 0,
    "memory_cache_hit_count": 20,
    "memory_cache_miss_count": 4,
    "disk_cache_hit_count": 1,
    "disk_cache_miss_count": 3,
    "series_sealed_count": 4,
    "validation_reject_count": 0,
    "gap_detection_count": 1,
    "average_fetch_latency_ms": 190.1,
    "max_fetch_latency_ms": 240.0,
    "error_rate": 0.0,
    "candles_served_count": 3200,
    "last_error_code": null,
    "per_timeframe": {
      "MINUTE_5": 3,
      "DAY_1": 1
    }
  },
  "metadata": {
    "runner_kind": "paper"
  }
}
```

---

## Appendix P — Security Review Prompts

1. Do cache files ever include access tokens or API secrets?
2. Are broker exceptions logged without Authorization headers?
3. Are path joins constrained to configured cache directories?
4. Can FORWARD_FILL be enabled in PRODUCTION via config bug?
5. Do fixtures avoid embedding live credentials?

---

## Appendix Q — Acceptance Scenarios (Definition of Done narrative)

1. **Cold fetch:** Client returns 100 five-minute bars; sealed series VALID; memory cache stores it.
2. **Cache hit:** Second identical fetch_range does not call client.
3. **Symbol resolve:** `NSE:NIFTY 50` style symbol resolves via injected resolver to token then fetches.
4. **OHLC reject:** high < low discarded; stats increment.
5. **Gap mark:** Missing bar yields PARTIAL + gap_after flag under MARK_GAP.
6. **Fail gaps:** FAIL policy raises `HD.VALIDATION.MISSING_CANDLES`.
7. **Latest N:** Returns ≤ N bars ascending.
8. **Session:** Only bars inside 09:15–15:30 IST for the date.
9. **Concurrency:** 16 readers + 1 refresher; no exceptions; no torn series.
10. **Boundary grep:** CI greps pass.

---

## Appendix R — Module Constants (reference)

```python
HISTORICAL_DATA_VERSION = "1.0.0"
HISTORICAL_DATA_SCHEMA_VERSION = "1.0.0"
PRODUCER_NAME = "broker.historical_data"

SUPPORTED_PRIMARY_UNDERLYINGS = frozenset({"NIFTY", "BANKNIFTY", "SENSEX"})
SUPPORTED_SECONDARY_UNDERLYINGS = frozenset({"FINNIFTY", "MIDCPNIFTY"})
SUPPORTED_INDEX_UNDERLYINGS = (
    SUPPORTED_PRIMARY_UNDERLYINGS | SUPPORTED_SECONDARY_UNDERLYINGS
)

TOPIC_SERIES_FETCHED = "market.historical.series.fetched"
TOPIC_SERIES_FAILED = "market.historical.series.failed"
TOPIC_CACHE_UPDATED = "market.historical.cache.updated"

DEFAULT_MEMORY_CACHE_MAX_ENTRIES = 256
DEFAULT_MEMORY_CACHE_TTL_SECONDS = 60.0
DEFAULT_DISK_CACHE_TTL_SECONDS = 86400.0
DEFAULT_MAX_CANDLES_PER_REQUEST = 20000
DEFAULT_BROKER_CHUNK_DAYS = 60
DEFAULT_MAX_GAP_RATIO = 0.05
DEFAULT_SESSION_OPEN = "09:15"
DEFAULT_SESSION_CLOSE = "15:30"
DEFAULT_SESSION_TIMEZONE = "Asia/Kolkata"
```

---

## Appendix S — Kite Interval Map (normative)

| CandleTimeframe | Kite `interval` string |
|---|---|
| `MINUTE_1` | `minute` |
| `MINUTE_3` | `3minute` |
| `MINUTE_5` | `5minute` |
| `MINUTE_10` | `10minute` |
| `MINUTE_15` | `15minute` |
| `MINUTE_30` | `30minute` |
| `MINUTE_60` | `60minute` |
| `DAY_1` | `day` |

**Rule MAP-HD-001:** No other interval strings are emitted in v1.0.

---

## Appendix T — Error-to-Outcome Mapping (quick reference)

| Failure | Fetch outcome | Cache pointer | Health |
|---|---|---|---|
| Config invalid at construct | exception | n/a | n/a |
| Broker IO failure | exception | previous retained | DEGRADED |
| Soft validation discards | success PARTIAL/VALID | new series cached | HEALTHY/DEGRADED |
| FAIL on gaps | exception | previous retained | DEGRADED |
| Empty required series | exception | previous/none | DEGRADED/UNHEALTHY |
| Disk corrupt | fallback fetch | memory may update | warning issue |

---

## Appendix U — Latest-N Lookback Heuristic

To satisfy `fetch_latest_n(..., n)` without over-fetching unbounded history:

```text
lookback = n * timeframe_duration
         + one full session length
         + weekend slack (2 days)
         + holiday slack (config, default 3 days)
from_ts = as_of - lookback
fetch_range(from_ts, as_of) → take last n
```

If fewer than n bars exist, return available bars (not an error) unless `require_exact_n=True` (optional config, default False).

---

## Appendix V — Corporate Action Metadata Shape (illustrative)

```json
{
  "corporate_actions": [
    {
      "symbol": "RELIANCE",
      "ex_date": "2026-07-01",
      "factor": 0.5,
      "action_type": "SPLIT"
    }
  ]
}
```

Injected via config/metadata provider — never hardcoded lists of production events inside the module source.

---

## Appendix W — Disk Cache File Naming

```text
{disk_cache_directory}/hd_{sha256(cache_key)[:16]}.json
```

Manifest optional: `hd_manifest.json` mapping cache_key → filename + written_at.

---

## Appendix X — Future NSE F&O Stocks Extension Path

1. Set `allow_equity_fo=True`.
2. Provide `enabled_equity_underlyings` via Application Configuration.
3. Resolve tokens via instrument_loader.
4. Prefer `APPLY_ADJUSTMENTS` or `REJECT_IF_UNADJUSTED` for equities spanning CA events.
5. No change to `HistoricalCandle` schema required.

---

## Appendix Y — Open Implementation Notes (non-blocking)

1. Holiday calendars may be injected as `Callable[[date], bool] is_trading_day`.
2. Broker maximum range per interval may require tighter `broker_chunk_days` for minute data.
3. OI may be absent for index cash/spot series; `oi=None` is valid.
4. If EventBus requires non-empty correlation_id, use `series_id` / `id_factory()`.

---

## Appendix Z — Changelog

| Version | Date | Notes |
|---|---|---|
| 1.0.0 | 2026-08-05 | Initial complete software engineering specification for `broker/historical_data.py`. |

---

**End of specification.**
