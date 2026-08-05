# Strategy Monitor Page — Software Engineering Specification

| Field | Value |
|---|---|
| **Scope** | Strategy Monitor page ↔ `DashboardFacade` ↔ strategy evaluation soft-reads |
| **Primary modules** | `dashboard/pages/strategy_monitor.py`, `dashboard/view_models.py`, `dashboard/dashboard_facade.py` |
| **Document version** | 1.0.0 |
| **Status** | Normative — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Implement the **Strategy Monitor** dashboard page for THETA AI TRADER v1.0.

The page visualizes **already-computed** strategy evaluation information through
`DashboardFacade` only:

1. **Recommendation banner**
2. **Header KPIs** — Market Regime, Active Strategy, Confidence, Evaluation Time
3. **Strategy ranking table** — Rank, Strategy, Score, Status, Reason, Eligible/Rejected
4. **Selected strategy details**
5. **Gate evaluation** table for the selected strategy
6. **Recommended option legs** table for the selected strategy

### Architecture freeze

- No broker logic in the page.
- No strategy computation / evaluation / scoring.
- No execution / order placement.
- No business logic — formatting and layout only.
- Read-only; thread-safe facade reads; typed; Google docstrings.
- Graceful offline mode (`—` / empty tables / info captions).

### Pipeline

```text
[Existing backend / live adapter soft-reads]
              ↓
[DashboardFacade]
    get_strategy_status()
    get_strategy_monitor()
              ↓
[dashboard/pages/strategy_monitor.py]
              ↓
[Operator browser]
```

---

## 2. Responsibilities

| # | Responsibility |
|---|---|
| R1 | Resolve Strategy Monitor data via facade only. |
| R2 | Render recommendation banner from facade fields. |
| R3 | Render header KPIs (regime / active / confidence / evaluation time). |
| R4 | Render four-family ranking table with Rank column. |
| R5 | Render selected strategy details (UI select over facade rows). |
| R6 | Render gate evaluation table for selected strategy. |
| R7 | Render recommended option legs table for selected strategy. |
| R8 | Soft-degrade when offline / empty. |
| R9 | Catch exceptions → error banner + placeholders; never crash. |

---

## 3. Non-Responsibilities

| # | Forbidden |
|---|---|
| NR1 | Broker quote APIs / WebSocket open |
| NR2 | Strategy evaluation / scoring / gate decisions |
| NR3 | Order placement / trading cycles |
| NR4 | Inventing eligibility, ranks, or legs not present upstream |
| NR5 | Importing `broker/*` or evaluating via `strategy/*` engines |

Rank display is a **presentation sort** of already-computed scores only.

---

## 4. Presentation model

Extend `StrategyMonitorView` / `StrategyRowView` (compatible additive fields):

| Field | Type | Source |
|---|---|---|
| `strategies` | `tuple[StrategyRowView, ...]` | four families from `get_strategy_status()` |
| `rank` (per row) | `str` | presentation sort of upstream scores |
| `gates` (per row) | `tuple[StrategyGateView, ...]` | soft-read `gates` / `gate_results` |
| `legs` (per row) | `tuple[StrategyLegView, ...]` | soft-read `legs` / `option_legs` / `selection` |
| `recommendation_banner` | `str` | soft-read banner / composed display label |
| header KPIs | existing | `market_regime`, `active_strategy`, … |
| `source` | `str` | `live` / `offline` |

`StrategyGateView`: `name`, `outcome`, `detail`.

`StrategyLegView`: `side`, `option_type`, `strike`, `quantity`, `symbol`, `delta`.

---

## 5. UI layout

1. Page header: “Strategy Monitor”
2. **Recommendation banner**
3. **KPI row** — regime / active / confidence / evaluation time
4. **Strategy ranking table**
5. **Selected strategy** — selectbox + detail KPIs / reasons
6. **Gate evaluation** table
7. **Recommended option legs** table

Offline:

- Banner / KPIs / scores show `—`
- Ranking table still lists four strategies with placeholders
- Gate / legs tables empty with column headers
- Info captions when awaiting backend

---

## 6. Files to touch

| File | Change |
|---|---|
| `docs/specifications/dashboard_strategy_monitor_page.md` | This specification |
| `dashboard/view_models.py` | Enrich monitor view models + helpers |
| `dashboard/dashboard_facade.py` | Soft-map gates, legs, ranks, banner |
| `dashboard/pages/strategy_monitor.py` | Full Strategy Monitor page UI |
| `dashboard/facade.py` | Null facade uses enriched empty mapping |
| `tests/test_dashboard_strategy_monitor.py` | Extend unit/smoke tests |

**Forbidden:** `broker/*`, `strategy/*` evaluation engines, execution modules.

---

## 7. Testing

| ID | Test |
|---|---|
| T01 | Offline StrategyMonitorView has four placeholder strategies |
| T02 | Offline banner / gates / legs are placeholders or empty |
| T03 | Live stub maps ranks, scores, gates, legs, banner |
| T04 | Selected strategy details resolve from active family |
| T05 | Page render offline without raise |
| T06 | No broker / strategy-engine imports on strategy monitor page |

---

## 8. Definition of Done

1. Strategy Monitor page visualizes strategy information from `DashboardFacade`.
2. Ranking table, selected details, gates, legs, and recommendation banner render.
3. Offline graceful; no crash.
4. Read-only; typed; Google docstrings; no broker/strategy/execution logic.

---

**End of specification — Strategy Monitor Page v1.0.0**
