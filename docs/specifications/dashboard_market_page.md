# Market Page — Software Engineering Specification

| Field | Value |
|---|---|
| **Scope** | Market page ↔ `DashboardFacade` ↔ market / regime soft-reads |
| **Primary modules** | `dashboard/pages/market.py`, `dashboard/view_models.py`, `dashboard/dashboard_facade.py` |
| **Document version** | 1.0.0 |
| **Status** | Normative — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Implement the **Market** dashboard page for THETA AI TRADER v1.0.

The page displays **already-computed** market context through `DashboardFacade` only:

1. **Live Index Cards** — NIFTY, BANKNIFTY, SENSEX, INDIA VIX
2. **Market Regime**
3. **Market Statistics** — LTP, Change, Volume, Connection, Last Update
4. **Market Snapshot** — selected underlying + option-chain table (display-only)
5. **TradingView Placeholder**

### Architecture freeze

- No broker logic in the page.
- No strategy computation / evaluation.
- No execution / order placement.
- No business logic — formatting and layout only.
- Read-only; thread-safe facade reads; typed; Google docstrings.
- Graceful offline mode (`—` / `OFFLINE` / empty tables).

### Pipeline

```text
[Existing backend / live adapter soft-reads]
              ↓
[DashboardFacade]
    get_home_market_indices()
    get_market_snapshot()
    get_strategy_status()   (regime companion)
              ↓
[dashboard/pages/market.py]
              ↓
[Operator browser]
```

---

## 2. Responsibilities

| # | Responsibility |
|---|---|
| R1 | Resolve market page data via facade only. |
| R2 | Render four live index cards (reuse index ticker component). |
| R3 | Render market regime. |
| R4 | Render market statistics KPIs. |
| R5 | Render market snapshot (underlying + option chain table). |
| R6 | Render TradingView placeholder. |
| R7 | Soft-degrade when offline / empty. |
| R8 | Catch exceptions → error banner + placeholders; never crash. |

---

## 3. Non-Responsibilities

| # | Forbidden |
|---|---|
| NR1 | Broker quote APIs / WebSocket open |
| NR2 | Strategy evaluation / scoring |
| NR3 | Order placement / trading cycles |
| NR4 | Computing regime or indicators in the UI |

---

## 4. Presentation model

Extend `MarketPageView` (compatible additive fields):

| Field | Type | Source |
|---|---|---|
| `indices` | `tuple[IndexQuoteView, ...]` | `get_home_market_indices()` |
| `market_regime` | `str` | `get_strategy_status().market_regime` or snapshot |
| `ltp` / `change` / `volume` | `str` | `get_market_snapshot()` |
| `selected_underlying` | `str` | snapshot |
| `underlyings` | `tuple[str, ...]` | snapshot (default four Home symbols offline) |
| `connection_status` | `str` | derived from indices / facade connection |
| `last_update` | `str` | snapshot `as_of` or index timestamps |
| `source` | `str` | `live` / `offline` |
| `option_chain_*` | existing | snapshot |

`market_page_statistic_cards(view)` returns KPI cards for statistics.

Presentation adapter `get_market_snapshot()` **must** compose indices + regime + statistics fields.

---

## 5. UI layout

1. Page header: “Market”
2. **Live Indices** — `render_index_strip(indices)`
3. **Market Regime** — KPI / badge
4. **Market Statistics** — LTP, Change, Volume, Connection, Last Update
5. **Market Snapshot** — disabled underlying select + option chain table
6. **Chart** — `render_tradingview_placeholder()`

Offline:

- Index cards show `—` / `OFFLINE`
- Regime / stats show `—`
- Empty option chain table with columns
- Info captions when awaiting backend

---

## 6. Files to touch

| File | Change |
|---|---|
| `docs/specifications/dashboard_market_page.md` | This specification |
| `dashboard/view_models.py` | Enrich `MarketPageView` + KPI helper |
| `dashboard/dashboard_facade.py` | Compose richer presentation market view; offline underlyings |
| `dashboard/pages/market.py` | Full Market page UI |
| `dashboard/facade.py` | Null facade returns enriched empty view |
| `tests/test_dashboard_market_page.py` | Unit/smoke tests |

**Forbidden:** `broker/*`, `strategy/*`, execution modules.

---

## 7. Testing

| ID | Test |
|---|---|
| T01 | Offline MarketPageView has four OFFLINE index cards |
| T02 | Offline regime/stats are placeholders |
| T03 | Live stub maps LTP/change/volume/regime/indices |
| T04 | Page render offline without raise |
| T05 | No broker/strategy imports on market page |
| T06 | Presentation adapter populates indices from home market API |

---

## 8. Definition of Done

1. Market page shows live index cards, regime, statistics, snapshot, TradingView placeholder.
2. All data via `DashboardFacade` only.
3. Offline graceful; no crash.
4. Read-only; typed; Google docstrings; no business/broker/strategy/execution logic.

---

**End of specification — Market Page v1.0.0**
