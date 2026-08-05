# Paper Trading Page — Software Engineering Specification

| Field | Value |
|---|---|
| **Scope** | Paper Trading page ↔ `DashboardFacade` ↔ paper ledger soft-reads |
| **Primary modules** | `dashboard/pages/paper_trading.py` |
| **Document version** | 1.0.0 |
| **Status** | Normative — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Implement the **Paper Trading** dashboard page for THETA AI TRADER v1.0.

The page visualizes **already-computed** paper ledger information through
`DashboardFacade` only:

1. **Paper account summary** — Cash, Used Margin, Available Margin, Equity, Today's PnL
2. **Open Positions** table
3. **Paper Orders** table
4. **Execution timeline**
5. **Performance summary** — Win Rate, Average Winner, Average Loser, Profit Factor, Expectancy

### Architecture freeze

- No broker access in the page.
- No strategy module access.
- No PnL calculation / simulation / order placement.
- No backend modifications.
- Read-only; typed; Google docstrings.
- Offline mode displays placeholders only (`—` / empty tables / info captions).

### Pipeline

```text
[Existing backend / live adapter soft-reads]
              ↓
[DashboardFacade]
    get_paper_trading()
    get_paper_trading_ledger()   (optional companion)
    get_analytics()             (optional performance soft-read)
              ↓
[dashboard/pages/paper_trading.py]
              ↓
[Operator browser]
```

---

## 2. Responsibilities

| # | Responsibility |
|---|---|
| R1 | Resolve paper trading data via facade only. |
| R2 | Render paper account summary KPIs. |
| R3 | Render open positions table. |
| R4 | Render paper orders table. |
| R5 | Render execution timeline (soft-read or order-derived display). |
| R6 | Render performance summary KPIs (soft-read only). |
| R7 | Soft-degrade when offline / empty. |
| R8 | Catch exceptions → error banner + placeholders; never crash. |

---

## 3. Non-Responsibilities

| # | Forbidden |
|---|---|
| NR1 | Broker quote / order APIs |
| NR2 | Strategy evaluation / scoring |
| NR3 | Computing PnL, win rate, expectancy, or profit factor |
| NR4 | Placing / cancelling paper or live orders |
| NR5 | Modifying Paper Trading Runner or backend modules |

---

## 4. Presentation mapping (display-only)

| UI field | Preferred facade source | Offline |
|---|---|---|
| Cash | `available_cash` / `virtual_cash` | `—` |
| Used Margin | `capital_used` / `used_margin` | `—` |
| Available Margin | `available_margin` / `available_cash` | `—` |
| Equity | `total_equity` | `—` |
| Today's PnL | `todays_pnl` | `—` |
| Positions | `positions` | empty table + headers |
| Orders | `orders` | empty table + headers |
| Execution timeline | `execution_timeline` or order timestamps | empty table + headers |
| Performance metrics | paper view attrs and/or `get_analytics()` | `—` |

Missing optional fields must never be invented — show `—`.

---

## 5. UI layout

1. Page header: “Paper Trading”
2. **Paper account summary** KPI row
3. **Open Positions** table
4. **Paper Orders** table
5. **Execution timeline** table
6. **Performance summary** KPI row

Offline:

- All money / performance values show `—`
- Tables render with column headers and zero rows
- Info captions when awaiting backend

---

## 6. Files to touch

| File | Change |
|---|---|
| `docs/specifications/dashboard_paper_trading_page.md` | This specification |
| `dashboard/pages/paper_trading.py` | Full Paper Trading page UI |
| `tests/test_dashboard_paper_trading_page.py` | Page unit/smoke tests |

**Forbidden:** other dashboard pages, `broker/*`, `strategy/*`, backend / paper runner modules.

---

## 7. Testing

| ID | Test |
|---|---|
| T01 | Offline view shows placeholder account KPIs |
| T02 | Offline positions / orders / timeline empty with headers |
| T03 | Offline performance KPIs are placeholders |
| T04 | Live stub renders all five panels |
| T05 | Page render offline without raise |
| T06 | Resolve uses DashboardFacade only; no broker/strategy imports |

---

## 8. Definition of Done

1. Paper Trading page visualizes account, positions, orders, timeline, and performance from `DashboardFacade`.
2. Offline graceful with placeholders only.
3. No PnL calculation, broker, strategy, or backend changes.
4. Typed; Google docstrings; page-only implementation.

---

**End of specification — Paper Trading Page v1.0.0**
