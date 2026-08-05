# Home Dashboard Market Data Integration — Software Engineering Specification

| Field | Value |
|---|---|
| **Scope** | Home page index strip ↔ `DashboardFacade` ↔ Market Data (via session only) |
| **Primary modules** | `dashboard/pages/home.py`, `dashboard/components/index_ticker.py`, `dashboard/view_models.py`, `dashboard/dashboard_facade.py` |
| **Document version** | 1.0.0 |
| **Status** | Draft — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-05 |

---

## 1. Purpose

This specification defines the **Home Trading Terminal market index display integration** for THETA AI TRADER v1.0.

The Home page must display live (or placeholder) quotes for:

- **NIFTY**
- **BANKNIFTY**
- **SENSEX**
- **INDIA VIX**

Each index card shows:

- **LTP** (last traded / last index value)
- **Absolute Change**
- **Percentage Change**
- **Last Update Timestamp**
- **Connection Status**

All values are consumed **only** through `dashboard/dashboard_facade.py` (`DashboardFacade` / `DashboardIntegrationFacade`). The dashboard **must not** collect market data, call brokers, evaluate strategies, or compute trading signals.

The feature answers: *"How does the Home page show index LTPs with change, timestamp, and connection status every second — using only the Dashboard Facade — without crashing when backend data is unavailable?"*

### Pipeline placement

```text
[Market Data Engine]  (UNCHANGED — already produces snapshots / quotes)
              ↓
[IntegrationSession]  (optional live session; public read accessors only)
              ↓
[dashboard/dashboard_facade.py]     ← ONLY dashboard→backend boundary
    get_home_market_indices()
    get_system_status()             (connection / market status context)
              ↓
[dashboard/pages/home.py]
[dashboard/components/index_ticker.py]
    render index strip + 1s autorefresh
              ↓
[Operator browser]
```

### Architecture freeze note

- **LOCKED:** Do not redesign the backend.
- **Do not** implement market data collection in the dashboard.
- **Do not** modify broker modules.
- **Do not** modify strategy modules.
- **Do not** modify Market Data Engine internals for this feature (consume existing public outputs via session → facade).
- Dashboard pages call **DashboardFacade methods only** — never engines, never Kite SDK, never WebSocket clients.
- Presentation remains free of business logic (formatting / layout / placeholders only).

### Goals

1. Extend Home index cards to show LTP, absolute change, percentage change, last update time, and connection status.
2. Refresh the Home market strip automatically **every 1 second** without forcing a trading cycle.
3. Show graceful placeholders (`"—"` / `UNKNOWN` / `OFFLINE`) when data is unavailable.
4. Route all reads through `DashboardFacade` exclusively.
5. Keep types immutable, methods documented (Google-style), and facade access thread-safe.
6. Preserve existing Home layout (KPI cards + TradingView placeholder) without redesign.

### Success criteria (Definition of Done)

- Home page shows **live market values** whenever backend data is available through the facade.
- Otherwise displays **offline placeholders** without crashing.
- NIFTY, BANKNIFTY, SENSEX, and INDIA VIX are always present in the strip (four cards).
- Autorefresh interval is **1.0 second** for the Home market strip.
- No broker / strategy / market-data-engine source edits required for offline DoD.
- Unit/smoke tests cover placeholder path, live mapping path, and autorefresh helper.

---

## 2. Responsibilities

| # | Responsibility | Description |
|---|---|---|
| R1 | **Facade home indices API** | Expose `get_home_market_indices()` on `DashboardIntegrationFacade` returning immutable DTOs. |
| R2 | **System status context** | Use `get_system_status()` for connection/market status labels when composing cards. |
| R3 | **View model enrichment** | Extend `IndexQuoteView` (or add `HomeIndexQuoteView`) with required display fields. |
| R4 | **Index ticker UI** | Update `index_ticker.py` to render all five fields per index. |
| R5 | **Home page wiring** | Home `render()` loads indices via facade only; enables 1s autorefresh. |
| R6 | **Placeholder policy** | Missing values → configured placeholder; never invent prices. |
| R7 | **Formatting only** | Format numbers/timestamps for display; no indicator or signal math. |
| R8 | **Thread-safe reads** | Rely on facade RLock; UI does not mutate shared quote state. |
| R9 | **Documentation** | Google-style docstrings on new public types/functions. |
| R10 | **Presentation adapter** | Map new facade DTO into `HomePageView.indices` for existing Protocol consumers. |

---

## 3. Non-Responsibilities

| # | Non-responsibility | Rationale |
|---|---|---|
| NR1 | **Collect ticks / open WebSockets** | Market Data Engine / streaming modules. |
| NR2 | **Call broker quote APIs** | Broker layer forbidden in dashboard. |
| NR3 | **Compute strategies / signals** | Strategy modules forbidden. |
| NR4 | **Calculate Greeks / IV / regime** | Analytical engines. |
| NR5 | **Start trading cycles on refresh** | Autorefresh = snapshot re-read only. |
| NR6 | **Modify broker or strategy packages** | Architecture locked. |
| NR7 | **Persist quotes to a database** | Out of scope. |
| NR8 | **Redesign Home layout** | Extend strip only. |

---

## 4. Canonical Symbols

| Display symbol | Facade symbol id | Notes |
|---|---|---|
| NIFTY | `NIFTY` | Primary index |
| BANKNIFTY | `BANKNIFTY` | Primary index |
| SENSEX | `SENSEX` | Primary index |
| INDIA VIX | `INDIA VIX` | Volatility index |

**Rule SYM-001:** Home strip **always** renders exactly these four symbols in this order.

**Rule SYM-002:** If upstream provides a subset, missing symbols are filled with placeholder cards (symbol present, values `"—"`).

**Rule SYM-003:** Symbol ids are case-sensitive display labels as above.

Constant:

```python
HOME_MARKET_INDEX_SYMBOLS: tuple[str, ...] = (
    "NIFTY",
    "BANKNIFTY",
    "SENSEX",
    "INDIA VIX",
)
```

---

## 5. Data Model

### 5.1 `HomeIndexQuote` (facade DTO, frozen)

Returned inside `FacadeHomeMarketIndices`.

| Field | Type | Description |
|---|---|---|
| `symbol` | `str` | One of `HOME_MARKET_INDEX_SYMBOLS` |
| `ltp` | `str` | Last price display (or `"—"`) |
| `change_abs` | `str` | Absolute change display (or `"—"`) |
| `change_pct` | `str` | Percentage change display (or `"—"`) |
| `last_update` | `str` | ISO/local formatted timestamp (or `"—"`) |
| `connection_status` | `str` | `LIVE` / `DELAYED` / `OFFLINE` / `UNKNOWN` |
| `schema_version` | `str` | `"1.0.0"` optional per-row; prefer parent stamp |

### 5.2 `FacadeHomeMarketIndices` (frozen)

| Field | Type | Description |
|---|---|---|
| `indices` | `tuple[HomeIndexQuote, ...]` | Length 4, ordered |
| `as_of` | `datetime` | Snapshot assembly time (tz-aware UTC) |
| `source` | `str` | `live` / `offline` / `cached` |
| `schema_version` | `str` | `"1.0.0"` |
| `market_status` | `str` | From `get_system_status().market_status` |
| `facade_connected` | `bool` | From `is_connected` |

### 5.3 Presentation `IndexQuoteView` (extend in place)

Extend existing `dashboard/view_models.py` `IndexQuoteView` **compatibly**:

| Field | Type | Default | Maps from |
|---|---|---|---|
| `symbol` | `str` | required | `HomeIndexQuote.symbol` |
| `value` | `str` | `"—"` | **LTP** (keep name for backward compat) |
| `change` | `str` | `"—"` | Combined or abs (see below) |
| `change_abs` | `str` | `"—"` | Absolute change |
| `change_pct` | `str` | `"—"` | Percentage change |
| `last_update` | `str` | `"—"` | Last update timestamp |
| `status` | `str` | `"UNKNOWN"` | Connection status |
| `connection_status` | `str` | `"UNKNOWN"` | Alias of status for clarity (same value) |

**Compat rule:** Existing `value` continues to mean LTP. `change` may show `"{change_abs} ({change_pct})"` for compact cards, while dedicated fields power the richer layout.

### 5.4 Placeholder invariant

**INV-HOME-MKT-001:** Unknown/missing numeric fields must be `"—"` — never fabricated `0.00` as a substitute for “no data”.

**INV-HOME-MKT-002:** Offline / disconnected → `connection_status="OFFLINE"` (or `"UNKNOWN"` if status itself unknown).

---

## 6. DashboardFacade API Extension

### 6.1 New method (normative)

```python
def get_home_market_indices(self) -> FacadeHomeMarketIndices:
    """Return the four Home terminal index quotes for display.

    Aggregates already-available upstream market/index fields via the
    injected session when present; otherwise returns offline placeholders.
    Does not fetch broker quotes or start trading cycles.
    """
```

### 6.2 Allowed companion reads (Home page)

Home page **may** call only:

| Method | Use |
|---|---|
| `get_home_market_indices()` | Primary index strip data |
| `get_system_status()` | Optional banner / connection context |
| `get_home_snapshot()` via presentation adapter | KPIs / existing home content |
| `refresh()` | Manual refresh path only (sidebar); not required every second if getter is live |

**Rule FAC-HOME-001:** Home market strip must not call `get_portfolio`, `get_risk`, `get_apme`, order APIs, or any non-facade module.

**Rule FAC-HOME-002:** Implementation of `get_home_market_indices` may internally read session accessors such as `get_market_snapshot`, `get_index_quotes`, or `get_home_market_indices` **if present** — soft-degrade when absent.

### 6.3 Upstream mapping (informative)

Prefer, in order:

1. `session.get_home_market_indices()` if available (pre-aggregated).
2. `session.get_index_quotes()` returning iterable of quote objects with fields (`symbol`, `ltp`/`last_price`, `change`/`net_change`, `change_percent`/`pchange`, `timestamp`/`exchange_timestamp`).
3. Partial fields from `session.get_market_snapshot()` when it embeds index map.
4. Else offline placeholders.

Connection status derivation (display-only heuristics — **not** trading gates):

| Condition | `connection_status` |
|---|---|
| Facade `is_connected` and quote has fresh timestamp | `LIVE` |
| Connected but timestamp stale per optional TTL hint from upstream metadata | `DELAYED` |
| Not connected / offline source | `OFFLINE` |
| Connected but no quote payload | `UNKNOWN` |

**Staleness:** If upstream provides `is_stale: bool` or `age_seconds`, map accordingly. Dashboard must **not** invent market calendars or exchange-hour logic beyond displaying the facade-provided status.

### 6.4 Formatting rules (presentation / facade boundary)

Facade returns **preformatted strings** for display stability:

| Field | Format example |
|---|---|
| `ltp` | `"24,512.40"` or `"—"` |
| `change_abs` | `"+85.20"` / `"-12.50"` / `"—"` |
| `change_pct` | `"+0.35%"` / `"-0.10%"` / `"—"` |
| `last_update` | `"2026-08-05 12:01:03 UTC"` or `"—"` |

Sign formatting is display mapping from upstream signed numbers — not a trading signal.

---

## 7. UI Specification

### 7.1 `components/index_ticker.py`

Update `render_index_strip(indices)` to render four responsive columns. Each card shows:

```text
┌─────────────────────┐
│ NIFTY               │
│ 24,512.40           │  ← LTP (value)
│ +85.20   +0.35%     │  ← abs + pct
│ 12:01:03 UTC        │  ← last_update
│ LIVE                │  ← connection_status badge
└─────────────────────┘
```

CSS classes (extend `assets/theme.css` as needed):

- `.theta-index-card`
- `.theta-index-symbol`
- `.theta-index-value` (LTP)
- `.theta-index-change-abs`
- `.theta-index-change-pct`
- `.theta-index-updated`
- `.theta-index-conn` (+ modifiers `.live` / `.offline` / `.delayed` / `.unknown`)

Positive change → `--theta-positive`; negative → `--theta-negative`; placeholder → muted.

### 7.2 `pages/home.py`

1. Call `page_header` as today.
2. Resolve market indices:
   - Prefer presentation facade `get_home_snapshot().indices` **after** adapter fills from `get_home_market_indices()`, **or**
   - If render context exposes `DashboardIntegrationFacade` directly, call `get_home_market_indices()` and map to `IndexQuoteView`.
3. `render_index_strip(...)`.
4. Existing KPI row + TradingView placeholder unchanged.
5. Enable **1 second** autorefresh for this page (see §8).

**Rule UI-HOME-001:** Catch facade exceptions → show error banner + placeholder strip; never raise to crash Streamlit.

### 7.3 Color / status badges

| `connection_status` | Badge class |
|---|---|
| `LIVE` | positive |
| `DELAYED` | warning |
| `OFFLINE` | negative / muted |
| `UNKNOWN` | neutral |

---

## 8. Automatic Refresh (1 second)

### 8.1 Requirement

Home market strip refreshes automatically every **1.0 second**.

### 8.2 Mechanism (Streamlit)

Use one of:

1. `streamlit-autorefresh` / `st_autorefresh(interval=1000, key="home_market_refresh")` when available, **or**
2. Existing `dashboard/utils/polling.py` extended with Home-specific helper wired in `home.render` / `app.py` when `active_page=="home"`.

**Normative defaults for Home market integration:**

| Setting | Value |
|---|---|
| Interval | `1.0` seconds |
| Scope | Home page only (do not force 1s globally if other pages prefer slower refresh) |
| Action | Re-run script → re-call facade getters |
| Trading cycle | **Forbidden** on autorefresh |

**Rule REF-001:** Autorefresh must not call `facade.start()`, must not place orders, must not run strategies.

**Rule REF-002:** Optional: call `facade.refresh()` only if TTL cache would otherwise serve stale home indices; preferred path is `cache_ttl_seconds=0` for home indices or TTL &lt; 1s.

### 8.3 Config knobs

Extend `DashboardUiConfig` (compatible additive fields):

| Field | Type | Default | Notes |
|---|---|---|---|
| `home_market_refresh_seconds` | `float` | `1.0` | Must be `> 0` |
| `enable_home_market_autorefresh` | `bool` | `True` | Feature flag |

Validation: `CFG-DASH-HOME-001` if `home_market_refresh_seconds <= 0`.

---

## 9. Presentation Adapter Mapping

`PresentationFacadeAdapter.get_home_snapshot()` **must** populate `HomePageView.indices` from `get_home_market_indices()`:

```text
for each HomeIndexQuote:
  IndexQuoteView(
    symbol=...,
    value=ltp,
    change=f"{change_abs} ({change_pct})" if both present else change_abs,
    change_abs=...,
    change_pct=...,
    last_update=...,
    status=connection_status,
    connection_status=connection_status,
  )
```

KPIs remain as currently implemented (placeholders / other facade reads) — out of scope except not breaking them.

---

## 10. Thread Safety & Typing

| Rule | Description |
|---|---|
| TS-001 | All facade home indices assembly under existing facade `RLock`. |
| TS-002 | DTOs frozen; Streamlit session stores only page id / UI prefs — not mutable quote dicts owned by engines. |
| TS-003 | Autorefresh reruns are single-threaded per session; concurrent sessions share process-safe facade instance. |
| TY-001 | Full type hints on public functions and dataclasses. |
| DOC-001 | Google-style docstrings on public API. |

---

## 11. Error Handling

| Condition | Behavior |
|---|---|
| Facade offline | Four placeholder cards; `connection_status=OFFLINE` |
| Partial upstream quotes | Fill available; placeholders for rest |
| Upstream exception | Soft degrade via facade; Home still renders |
| Autorefresh library missing | Log warning once; manual Refresh still works; page does not crash |

Error codes (facade):

| Code | Meaning |
|---|---|
| `DIF.HOME_MARKET.UNAVAILABLE` | No index payload (offline/empty) |
| `DIF.HOME_MARKET.PARTIAL` | Warning metadata only (optional) |
| `DIF.UPSTREAM.ERROR` | Existing upstream error path |

---

## 12. Files to Touch (implementation checklist)

| File | Change |
|---|---|
| `dashboard/dashboard_facade.py` | Add `HomeIndexQuote`, `FacadeHomeMarketIndices`, `get_home_market_indices()`, empty factory, mapping |
| `dashboard/view_models.py` | Extend `IndexQuoteView` fields |
| `dashboard/components/index_ticker.py` | Richer card layout |
| `dashboard/pages/home.py` | Wire facade + 1s autorefresh |
| `dashboard/assets/theme.css` | Badge/change styles |
| `dashboard/config.py` | Home refresh settings |
| `dashboard/utils/polling.py` | Optional 1s home helper |
| `tests/test_dashboard_home_market.py` | New tests |

**Forbidden to modify for this feature:** `broker/*`, `strategy/*`, Market Data Engine internals (unless a pre-existing public session accessor already exists — consume only).

---

## 13. Testing Requirements

| ID | Test |
|---|---|
| T01 | Offline facade returns 4 symbols with `"—"` fields and `OFFLINE` |
| T02 | Symbol order is NIFTY → BANKNIFTY → SENSEX → INDIA VIX |
| T03 | Live stub session maps LTP/change/pct/timestamp/status |
| T04 | Partial upstream fills missing symbols with placeholders |
| T05 | `IndexQuoteView.value` equals LTP |
| T06 | `render_index_strip` does not raise on placeholders (mocked `st`) |
| T07 | Home refresh interval config defaults to `1.0` |
| T08 | Autorefresh helper returns True after 1s elapsed |
| T09 | No broker/strategy imports in home/index_ticker/facade home path |
| T10 | Presentation adapter populates `HomePageView.indices` from new API |

Coverage: extend facade tests for new method; smoke-test home render.

---

## 14. Definition of Done

Complete when all are true:

1. Home page displays NIFTY, BANKNIFTY, SENSEX, INDIA VIX.
2. Each card shows LTP, absolute change, percentage change, last update timestamp, connection status.
3. Values refresh automatically every second when autorefresh is enabled.
4. Unavailable data shows graceful placeholders without crashing.
5. All data flows through `DashboardFacade` only.
6. No business logic; no broker/strategy/market-collection code in dashboard.
7. Fully typed; Google docstrings; thread-safe facade reads.
8. Backend architecture remains locked and unmodified beyond optional consumption of existing session read APIs.

---

## Appendix A — Wireframe

```text
┌──────────┬──────────┬──────────┬──────────┐
│ NIFTY    │ BANKNIFTY│ SENSEX   │ INDIA VIX│
│ 24512.40 │ 52100.15 │ 81234.50 │ 13.22    │
│ +85.20   │ -120.40  │ +—       │ -0.15    │
│ +0.35%   │ -0.23%   │ —        │ -1.12%   │
│ 12:01:03 │ 12:01:03 │ —        │ 12:01:02 │
│ LIVE     │ LIVE     │ OFFLINE  │ LIVE     │
└──────────┴──────────┴──────────┴──────────┘
        (autorefresh every 1.0s)
```

---

## Appendix B — Out of Scope

- TradingView live overlays
- Option-chain Home widgets
- User-configurable index universe beyond the four symbols
- Push WebSocket UI without Streamlit rerun
- Exchange holiday calendars inside dashboard

---

**End of specification — Home Dashboard Market Data Integration v1.0.0**
