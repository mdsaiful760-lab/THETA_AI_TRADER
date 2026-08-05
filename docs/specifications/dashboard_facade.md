# Dashboard Integration Facade — Software Engineering Specification

| Field | Value |
|---|---|
| **Module** | `dashboard/integration_facade.py` |
| **Document version** | 1.0.0 |
| **Status** | Draft — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-05 |

---

## 1. Purpose

`dashboard/integration_facade.py` defines the **single read-only aggregation interface** between the frozen THETA AI TRADER backend and the Streamlit dashboard presentation layer.

The facade answers: *"Given an optional live `IntegrationSession` (or offline mode), how does the dashboard retrieve system status, market context, strategy status, paper positions, orders, portfolio, risk, APME, logs, and performance — from one thread-safe, fully typed, versioned interface — without embedding business logic in the UI?"*

It **aggregates and adapts** already-computed backend artifacts into immutable dashboard DTOs.

It **must not** implement trading logic, compute strategies, place orders, calculate risk, mutate portfolios, or talk to brokers directly.

### Pipeline placement

```text
[Frozen backend engines — UNCHANGED]
    Market Data · Strategy Evaluation · Trade Decision
    Risk · Execution · Order Manager / Paper Runner
    Position · Portfolio · APME · Event Bus
              ↓
[system/integration_engine.py]
    IntegrationSession (composition facade — start/stop/health)
              ↓
[dashboard/integration_facade.py]          ← THIS MODULE
    DashboardIntegrationFacade
    read-only getters → immutable versioned DTOs
    optional thin adapter → dashboard presentation Protocol
              ↓
[dashboard/app.py · pages · components]
    Streamlit presentation only
```

### Architecture freeze note

The platform architecture is **FROZEN** for v1.0:

- **No backend redesign.** Engines, orchestrator, brokers, and strategies remain unchanged.
- **No new analytical engines.** This module is presentation-facing plumbing only.
- **No broker modification.** Broker status is read via `IntegrationSession` health/runtime only.
- **No strategy modification.** Strategy status is read from evaluation/decision snapshots only.
- Dashboard pages consume this facade (directly or via the existing `DashboardBackendFacade` adapter) and never call engines.
- Existing `dashboard/facade.py` (`NullIntegrationFacade`, presentation Protocol) remains the offline/UI Protocol layer; this module is the **canonical live aggregation facade** that fills those view models from backend snapshots.

### Goals

1. Provide **one interface** for all dashboard information needs listed in §2.
2. Remain **strictly read-only** for market/strategy/portfolio/risk/APME/log/performance data.
3. Be **thread-safe** under concurrent Streamlit sessions / refresh calls.
4. Use **fully typed**, **immutable** (`frozen=True`) DTOs with Google-style docstrings.
5. Support **versioned schema** `1.0.0` for all public payloads.
6. Provide **health reporting** for the facade itself and passthrough system health.
7. Support **offline / disconnected** mode with explicit placeholders — never fabricate metrics.
8. Avoid **global mutable state**; inject session/clock/config at construction.
9. Keep **zero business logic** — map, format, redact, and assemble only.
10. Integrate cleanly with `docs/specifications/dashboard_foundation.md` without redesigning pages.

### Success criteria (Definition of Done)

- Dashboard can retrieve **all required information** from `DashboardIntegrationFacade` alone.
- Public getters listed in §2 exist, are typed, documented, and return immutable DTOs.
- Unit tests cover mapping, thread safety, offline mode, schema version, and forbidden-behaviour guards.
- No modifications to broker, strategy, risk, or unrelated backend modules required for this facade to compile and run in offline mode.
- Live mode only **reads** public Integration Session / snapshot APIs — never mutates trading state except optional lifecycle delegation documented separately (out of core read API).

### Relationship to other modules

| Module | Relationship |
|---|---|
| `system/integration_engine.py` | **Primary upstream.** Optional injected `IntegrationSession` for health/runtime/snapshots. |
| `dashboard/facade.py` | **Sibling presentation Protocol.** May wrap this facade; `NullIntegrationFacade` covers offline UI today. |
| `dashboard/view_models.py` | **DTO consumers / mappers.** Facade may return foundation view models or facade-native DTOs that map 1:1. |
| `dashboard/app.py` | **Consumer.** Obtains facade instance via dependency injection / session. |
| Market / Strategy / Risk / Portfolio / APME / Paper / Orders | **Read-only sources** via session public getters — never imported for mutation APIs. |
| Broker / Kite SDK | **Forbidden.** |

### Distinction from Integration Engine

| Concern | Integration Engine | Dashboard Integration Facade |
|---|---|---|
| Role | Composition root; wires engines | Read-only aggregation for UI |
| Constructs engines | Yes | **Never** |
| Start/stop process | Yes | May **delegate** only; not required for read getters |
| UI DTOs / formatting | **Never** | Yes (display mapping only) |

### Distinction from Streamlit pages

| Concern | Pages / components | This facade |
|---|---|---|
| Rendering | Yes | **Never** |
| Widget state | Yes | **Never** |
| Data retrieval | Via facade only | Authoritative read API |

---

## 2. Public Read API (Normative)

The facade **must** expose the following methods. Names are stable for v1.0.0.

| Method | Returns | Description |
|---|---|---|
| `get_system_status()` | `FacadeSystemStatus` | System + facade health, broker connectivity summary, execution mode, market status label. |
| `get_market_snapshot()` | `FacadeMarketSnapshot` | Underlying list, selected quote placeholders/fields, option-chain tabular rows already produced upstream (or empty). |
| `get_strategy_status()` | `FacadeStrategyStatus` | Registered / last-evaluated strategy rows (id, family, status, confidence, signal, timestamp, reasons). |
| `get_paper_positions()` | `FacadePaperPositions` | Virtual cash, realized/unrealized PnL strings, paper position rows, optional equity series points. |
| `get_order_book()` | `FacadeOrderBook` | Recent order / paper-order summary rows (read-only). |
| `get_portfolio()` | `FacadePortfolio` | Portfolio metrics + position rows + optional chart series. |
| `get_risk()` | `FacadeRisk` | Last risk verdict summary, reason codes, redacted limit labels. |
| `get_apme()` | `FacadeApme` | Latest APME decision summaries and per-position management hints (informational). |
| `get_logs()` | `FacadeLogs` | Bounded recent log/event lines (redacted). |
| `get_performance()` | `FacadePerformance` | Analytics/performance aggregates **only if** supplied by backend; otherwise empty placeholders. |

### 2.1 Additional required surface

| Member | Type | Description |
|---|---|---|
| `schema_version` | `str` property | Always `"1.0.0"`. |
| `get_facade_health()` | `FacadeHealthReport` | Facade-local health (connected, cache age, last error). |
| `refresh()` | `FacadeRefreshResult` | Invalidate caches and re-read upstream snapshots — **no trading cycle**. |
| `is_connected` | `bool` property | Whether a live Integration Session is attached and reporting connected. |

### 2.2 Explicit non-API (forbidden)

The facade **must not** expose:

- `place_order`, `cancel_order`, `modify_order`
- `evaluate_strategy`, `select_strategy`, `score_strategy`
- `compute_risk`, `approve_trade`
- `simulate_fill` / direct paper runner mutation APIs (beyond reading sealed snapshots)
- Broker connect/auth credential APIs

Lifecycle `start()` / `stop()` **may** be provided as thin passthrough to `IntegrationSession` for sidebar controls, but are **not** part of the core read API DoD. If present, they must not contain business logic.

---

## 3. Responsibilities

| # | Responsibility | Description |
|---|---|---|
| R1 | **Single read interface** | Aggregate all dashboard data needs behind one class. |
| R2 | **DTO assembly** | Map upstream snapshots → immutable facade DTOs. |
| R3 | **Placeholder policy** | Use `"—"` / empty collections when data absent — never invent numbers. |
| R4 | **Redaction** | Strip secrets from settings/logs before return. |
| R5 | **Thread safety** | Guard cache and upstream reads with `threading.RLock`. |
| R6 | **Versioned schema** | Stamp every payload with `schema_version="1.0.0"`. |
| R7 | **Health reporting** | Expose system status + facade health. |
| R8 | **Offline mode** | Operate with `session=None` via empty snapshots. |
| R9 | **Cache optional** | Optional TTL cache for expensive snapshot reads; refresh clears it. |
| R10 | **Formatting only** | Money/percent/timestamp formatting for display strings — no domain formulas. |
| R11 | **Google docstrings** | Public class and methods documented. |
| R12 | **Adapter hook** | Provide `as_presentation_facade()` or documented mapping to foundation Protocol. |

---

## 4. Non-Responsibilities

| # | Non-responsibility | Rationale |
|---|---|---|
| NR1 | **Compute strategies or signals** | Strategy Evaluation Engine. |
| NR2 | **Place or manage orders** | Order Manager / broker. |
| NR3 | **Calculate risk verdicts** | Risk Engine. |
| NR4 | **Run APME rules** | APME. |
| NR5 | **Simulate paper fills / capital** | Paper Trading Runner. |
| NR6 | **Fetch market data from broker/WebSocket** | Market Data Engine. |
| NR7 | **Construct engines or orchestrator** | Integration Engine. |
| NR8 | **Render Streamlit UI** | Dashboard pages/components. |
| NR9 | **Persist database state** | External persistence. |
| NR10 | **Modify backend / broker / strategy modules** | Architecture locked. |
| NR11 | **Hold global mutable singletons** | Inject dependencies. |
| NR12 | **Force trading cycles on refresh** | Refresh = snapshot re-read only. |

---

## 5. Module Layout

```text
dashboard/
├── integration_facade.py     ← THIS MODULE (canonical aggregation facade)
├── facade.py                 ← existing presentation Protocol + NullIntegrationFacade
├── view_models.py            ← presentation DTOs (may be reused or mapped)
└── ...
```

**Recommended implementation file:** `dashboard/integration_facade.py`

**Exports:**

```text
DASHBOARD_FACADE_SCHEMA_VERSION = "1.0.0"
DashboardIntegrationFacade
DashboardIntegrationFacadeConfig
FacadeSystemStatus
FacadeMarketSnapshot
FacadeStrategyStatus
FacadePaperPositions
FacadeOrderBook
FacadePortfolio
FacadeRisk
FacadeApme
FacadeLogs
FacadePerformance
FacadeHealthReport
FacadeRefreshResult
empty_* factory helpers
```

---

## 6. Configuration

### 6.1 `DashboardIntegrationFacadeConfig` (frozen)

| Field | Type | Default | Constraints |
|---|---|---|---|
| `schema_version` | `str` | `"1.0.0"` | Must equal constant |
| `cache_ttl_seconds` | `float` | `0.0` | `>= 0` (`0` = no TTL cache) |
| `log_limit_default` | `int` | `200` | `>= 1` |
| `placeholder` | `str` | `"—"` | Non-empty |
| `redact_secret_keys` | `tuple[str, ...]` | see §11 | Lowercase key fragments |
| `enable_lifecycle_passthrough` | `bool` | `False` | If True, allow start/stop delegation |
| `metadata` | `Mapping[str, str]` | `{}` | Audit |

Invalid config raises `DashboardFacadeConfigurationError` with codes `CFG-DIF-*`.

### 6.2 Construction

```python
class DashboardIntegrationFacade:
    def __init__(
        self,
        session: IntegrationSessionLike | None = None,
        *,
        config: DashboardIntegrationFacadeConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create a read-only dashboard integration facade.

        Args:
            session: Optional live Integration Session. ``None`` enables offline mode.
            config: Frozen facade configuration.
            clock: Injectable clock for cache timestamps and tests.
        """
```

**Rule CTR-001:** No module-level mutable facade instance.

**Rule CTR-002:** `session` is stored as a weak reference or plain reference but **never mutated** by getters.

---

## 7. Data Model (Immutable DTOs)

All public DTOs are `@dataclass(frozen=True)` and include:

| Common field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | `"1.0.0"` |
| `as_of` | `datetime` | Timezone-aware UTC |
| `source` | `str` | `"live"` \| `"offline"` \| `"cached"` |

### 7.1 `FacadeSystemStatus`

| Field | Type | Description |
|---|---|---|
| `system_status` | `str` | `RUNNING` / `STOPPED` / `DEGRADED` / `DISCONNECTED` / `UNKNOWN` |
| `broker_status` | `str` | `CONNECTED` / `DISCONNECTED` / `N/A` |
| `execution_mode` | `str` | `PAPER` / `LIVE` / `ANALYSIS` / `BACKTEST` |
| `market_status` | `str` | `OPEN` / `CLOSED` / `UNKNOWN` |
| `message` | `str` | Human-readable summary |
| `facade_healthy` | `bool` | Facade operational flag |

### 7.2 `FacadeMarketSnapshot`

| Field | Type | Description |
|---|---|---|
| `underlyings` | `tuple[str, ...]` | Display list |
| `selected_underlying` | `str` | Placeholder or selection label |
| `ltp` / `change` / `volume` | `str` | Display strings |
| `option_chain_columns` | `tuple[str, ...]` | Column headers |
| `option_chain_rows` | `tuple[tuple[str, ...], ...]` | Preformatted rows |

### 7.3 `FacadeStrategyStatus`

| Field | Type | Description |
|---|---|---|
| `strategies` | `tuple[FacadeStrategyRow, ...]` | Rows |

`FacadeStrategyRow`: `strategy_id`, `family`, `status`, `confidence`, `last_signal`, `timestamp`, `reasons: tuple[str, ...]`.

### 7.4 `FacadePaperPositions`

| Field | Type | Description |
|---|---|---|
| `virtual_cash` | `str` | Display |
| `realized_pnl` | `str` | Display |
| `unrealized_pnl` | `str` | Display |
| `positions` | `tuple[FacadePaperPositionRow, ...]` | Rows |
| `equity_series` | `tuple[tuple[str, float], ...]` | Optional chart points from upstream only |

### 7.5 `FacadeOrderBook`

| Field | Type | Description |
|---|---|---|
| `orders` | `tuple[FacadeOrderRow, ...]` | Recent orders |

`FacadeOrderRow`: `order_id`, `plan_id`, `status`, `symbol`, `side`, `quantity`, `timestamp`.

### 7.6 `FacadePortfolio`

| Field | Type | Description |
|---|---|---|
| `equity` / `exposure` / `utilization` | `str` | Metrics |
| `positions` | `tuple[FacadePortfolioPositionRow, ...]` | Rows |
| `equity_series` / `allocation_series` | tuples | Optional Plotly inputs |

### 7.7 `FacadeRisk`

| Field | Type | Description |
|---|---|---|
| `verdict` | `str` | `APPROVED` / `REJECTED` / `SKIPPED` / `—` |
| `reason_codes` | `tuple[str, ...]` | Codes |
| `limits` | `tuple[tuple[str, str], ...]` | Redacted label/value pairs |

### 7.8 `FacadeApme`

| Field | Type | Description |
|---|---|---|
| `summary` | `str` | Informational banner text |
| `decisions` | `tuple[FacadeApmeDecisionRow, ...]` | Rows |

### 7.9 `FacadeLogs`

| Field | Type | Description |
|---|---|---|
| `entries` | `tuple[FacadeLogEntry, ...]` | Newest-last or newest-first — document choice; v1 newest-first |
| `limit` | `int` | Applied limit |

`FacadeLogEntry`: `timestamp`, `level`, `message` (redacted), `logger`.

### 7.10 `FacadePerformance`

| Field | Type | Description |
|---|---|---|
| `metrics` | `tuple[tuple[str, str], ...]` | Label/value placeholders or upstream aggregates |
| `series` | `tuple[tuple[str, float], ...]` | Optional |

### 7.11 `FacadeHealthReport`

| Field | Type | Description |
|---|---|---|
| `connected` | `bool` | Live session attached |
| `status` | `str` | `HEALTHY` / `DEGRADED` / `OFFLINE` |
| `cache_entries` | `int` | Cached keys |
| `last_refresh_at` | `datetime \| None` | Last successful refresh |
| `last_error_code` | `str \| None` | Last mapped error |
| `schema_version` | `str` | `"1.0.0"` |

### 7.12 Placeholder invariant

**INV-PLACE-001:** When upstream value is missing, numeric display fields must be the configured placeholder string (default `"—"`), never `"0"` invented as a substitute for unknown, and never random/demo PnL.

---

## 8. Upstream Mapping Contract

### 8.1 Offline (`session is None`)

Every getter returns empty/placeholder DTOs with `source="offline"` and `system_status="DISCONNECTED"`.

### 8.2 Live (`IntegrationSessionLike`)

Define a structural Protocol (no hard dependency on unfinished session methods):

```python
class IntegrationSessionLike(Protocol):
    def get_health(self) -> object: ...
    def get_runtime_state(self) -> object: ...
    # Optional snapshot accessors — if absent, facade returns placeholders
    def get_market_snapshot(self) -> object: ...  # optional
    ...
```

**Rule MAP-001:** Missing optional accessors → placeholders, not exceptions (unless `strict=True` config extension in v1.1).

**Rule MAP-002:** Facade catches upstream exceptions, records `last_error_code`, returns degraded placeholders, and sets `facade_healthy=False` where appropriate.

**Rule MAP-003:** Mapping is pure adaptation: attribute reads, `str()` formatting, tuple materialization, redaction. No scoring, no Greeks, no sizing.

### 8.3 Suggested field mapping (informative)

| Facade method | Prefer upstream | Fallback |
|---|---|---|
| `get_system_status` | `session.get_health()`, `get_runtime_state()` | offline defaults |
| `get_market_snapshot` | market snapshot / stream cache view | empty chain |
| `get_strategy_status` | strategy evaluation bundle summary | empty strategies |
| `get_paper_positions` | paper runner capital/position snapshots | empty |
| `get_order_book` | order tracker summaries / paper order events | empty |
| `get_portfolio` | portfolio manager snapshot | empty |
| `get_risk` | last risk decision result | verdict `—` |
| `get_apme` | last APME decision report | empty decisions |
| `get_logs` | session log ring / event bus buffer | empty |
| `get_performance` | analytics aggregates if any | empty metrics |

---

## 9. Thread Safety

| Rule | Description |
|---|---|
| TS-001 | Instance methods that touch `_cache` or `_last_*` acquire `self._lock` (`threading.RLock`). |
| TS-002 | Returned DTOs are immutable; callers may share freely across threads. |
| TS-003 | Do not expose internal mutable dicts/lists. |
| TS-004 | Do not use module-level mutable caches. |
| TS-005 | `refresh()` clears cache under the same lock. |

---

## 10. Caching

- Default `cache_ttl_seconds=0` → every call hits upstream adapter (still under lock for `last_error` bookkeeping).
- When TTL > 0, cache key = method name (+ args for `get_logs(limit=...)`).
- Cache stores immutable DTOs only.
- `refresh()` invalidates all keys and updates `last_refresh_at`.

**Rule CACHE-001:** Cache never invents data; it only memoizes prior successful mappings.

---

## 11. Redaction

Before returning logs or any settings-adjacent payloads:

- Redact values whose keys contain fragments in `redact_secret_keys` default:
  `("token", "secret", "password", "api_key", "apikey", "access_key", "auth")`
- Replace with `"***"`.
- Truncate oversized log messages (e.g. 2_000 chars).

---

## 12. Error Taxonomy

| Code | Meaning |
|---|---|
| `DIF.SESSION.UNAVAILABLE` | No live session |
| `DIF.UPSTREAM.ERROR` | Upstream read raised |
| `DIF.UPSTREAM.UNSUPPORTED` | Optional accessor missing |
| `DIF.CACHE.INVALID` | Cache corruption / type mismatch (fail closed → bypass cache) |
| `DIF.CONFIG.INVALID` | Bad config |
| `CFG-DIF-001`… | Config field invariants |

Exceptions:

| Type | When |
|---|---|
| `DashboardFacadeError` | Base |
| `DashboardFacadeConfigurationError` | Config validation |
| `DashboardFacadeValidationError` | DTO/invariant failure (rare; prefer soft degrade) |

Getters prefer **soft degrade** over raising, except constructors/config.

---

## 13. Serialization

- Schema version `1.0.0`.
- Provide `to_jsonable(dto) -> Mapping[str, object]` for tests/audit.
- `Decimal` → `str`; `datetime` → ISO-8601 with offset; tuples → lists.
- Round-trip of full live OrderTracker objects is **not** required; facade DTOs only.

---

## 14. Presentation Adapter (compatibility)

To avoid redesigning the Streamlit foundation:

```text
DashboardIntegrationFacade
    → PresentationFacadeAdapter (optional thin class in same module or facade.py)
    → implements DashboardBackendFacade Protocol methods by delegating:
         get_health ← get_system_status
         get_market_snapshot ← get_market_snapshot
         get_strategy_monitor ← get_strategy_status
         get_paper_trading ← get_paper_positions
         get_orders ← get_order_book
         get_portfolio ← get_portfolio
         get_risk ← get_risk
         get_apme ← get_apme
         get_logs ← get_logs
         get_analytics ← get_performance
```

**Rule ADAPT-001:** Adapter performs field renaming only; no extra logic.

---

## 15. Public Class Sketch

```python
class DashboardIntegrationFacade:
    """Read-only aggregation facade between backend session and dashboard UI."""

    @property
    def schema_version(self) -> str:
        """Return facade DTO schema version."""

    @property
    def is_connected(self) -> bool:
        """Return whether a live backend session is attached."""

    def get_facade_health(self) -> FacadeHealthReport:
        """Return facade-local health report."""

    def get_system_status(self) -> FacadeSystemStatus:
        """Return aggregated system, broker, mode, and market status."""

    def get_market_snapshot(self) -> FacadeMarketSnapshot:
        """Return market display snapshot (quotes/chain placeholders or upstream rows)."""

    def get_strategy_status(self) -> FacadeStrategyStatus:
        """Return strategy evaluation status rows for the monitor page."""

    def get_paper_positions(self) -> FacadePaperPositions:
        """Return paper capital and position display rows."""

    def get_order_book(self) -> FacadeOrderBook:
        """Return recent order summary rows (read-only)."""

    def get_portfolio(self) -> FacadePortfolio:
        """Return portfolio metrics and position rows."""

    def get_risk(self) -> FacadeRisk:
        """Return last risk verdict summary and redacted limits."""

    def get_apme(self) -> FacadeApme:
        """Return informational APME decision summaries."""

    def get_logs(self, *, limit: int | None = None) -> FacadeLogs:
        """Return bounded, redacted log entries."""

    def get_performance(self) -> FacadePerformance:
        """Return performance/analytics aggregates when available."""

    def refresh(self) -> FacadeRefreshResult:
        """Invalidate caches and re-read upstream snapshots without trading."""
```

---

## 16. Testing Requirements

| ID | Test |
|---|---|
| T01 | Offline mode: all ten getters return schema `1.0.0` and placeholders |
| T02 | `get_system_status` offline → `DISCONNECTED` |
| T03 | Config invariant failures raise `CFG-DIF-*` |
| T04 | Thread safety: concurrent getter calls do not corrupt cache |
| T05 | `refresh()` clears TTL cache |
| T06 | Upstream exception → degraded DTO + health error code |
| T07 | Log redaction masks token-like content |
| T08 | Presentation adapter maps methods without altering placeholder policy |
| T09 | No broker/strategy/risk engine imports in module import graph |
| T10 | `get_logs(limit=n)` respects bound |
| T11 | DTOs are frozen (mutation raises) |
| T12 | DoD smoke: one facade instance supplies all ten information domains |

Coverage target: **≥ 95%** on `dashboard/integration_facade.py`.

---

## 17. Definition of Done

Complete when **all** are true:

1. Spec implemented in `dashboard/integration_facade.py` (follow-on implementation task).
2. All methods in §2 exist and are fully typed with Google-style docstrings.
3. Thread-safe; no global mutable state.
4. Read-only; no strategy computation; no order placement; no business logic.
5. Versioned schema `1.0.0` on payloads.
6. Health reporting via `get_system_status` + `get_facade_health`.
7. Dashboard can retrieve **all required information from this one interface**.
8. Backend, broker, and strategy modules remain unmodified.
9. Unit tests prove offline DoD and boundary guards.

---

## Appendix A — Method ↔ Dashboard Page Map

| Page | Facade method |
|---|---|
| Sidebar status | `get_system_status` |
| Home / Market | `get_market_snapshot` (+ home may also use system/paper) |
| Strategy Monitor | `get_strategy_status` |
| Paper Trading | `get_paper_positions` |
| Orders | `get_order_book` |
| Portfolio | `get_portfolio` |
| Risk | `get_risk` |
| APME | `get_apme` |
| Logs | `get_logs` |
| Analytics | `get_performance` |

---

## Appendix B — Out of Scope

- Push/WebSocket fan-out into Streamlit
- Write APIs for orders/settings secrets
- Redesign of dashboard pages
- Changes to Integration Engine internals beyond consuming public session APIs
- Fabricating demo market prices for screenshots

---

## Appendix C — Implementer Checklist

- [ ] Create `dashboard/integration_facade.py` with frozen DTOs + facade class
- [ ] Offline factories for all ten getters
- [ ] Optional session adapter with soft degrade
- [ ] RLock + optional TTL cache + `refresh()`
- [ ] Redaction helpers
- [ ] Presentation Protocol adapter
- [ ] Tests ≥ 95% coverage
- [ ] Confirm zero unrelated module edits

---

**End of specification — Dashboard Integration Facade v1.0.0**
