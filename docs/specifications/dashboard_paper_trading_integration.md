# Paper Trading Dashboard Integration — Software Engineering Specification

| Field | Value |
|---|---|
| **Scope** | Paper Trading page ↔ `DashboardFacade` ↔ Paper Trading Runner state (via session only) |
| **Primary modules** | `dashboard/pages/paper_trading.py`, `dashboard/view_models.py`, `dashboard/dashboard_facade.py`, `dashboard/config.py`, `dashboard/utils/polling.py` |
| **Document version** | 1.0.0 |
| **Status** | Normative — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-05 |

---

## 1. Purpose

This specification defines the **Paper Trading page integration** for THETA AI TRADER v1.0.

The Paper Trading page must display **already-computed** paper ledger state:

**Capital / PnL KPIs**

- Available Cash
- Capital Used
- Total Equity
- Today's P&L
- Realized P&L
- Unrealized P&L

**Position table columns**

- Symbol
- Strategy
- Qty
- Entry
- Current
- MTM
- Status

**Order status counts**

- Filled
- Pending
- Cancelled
- Rejected

All values are consumed **only** through `dashboard/dashboard_facade.py` (`DashboardFacade` / `DashboardIntegrationFacade`). The dashboard **must not** place trades, simulate fills, modify the Paper Trading Runner, or invent PnL.

The feature answers: *"How does the Paper Trading page show cash, equity, PnL, positions, and order counts every second — using only the Dashboard Facade — without implementing trading logic?"*

### Pipeline placement

```text
[Paper Trading Runner]  (UNCHANGED — already produces capital / positions / fills)
              ↓
[IntegrationSession]  (optional live session; public read accessors only)
              ↓
[dashboard/dashboard_facade.py]     ← ONLY dashboard→backend boundary
    get_paper_trading_ledger()
    get_paper_positions() / get_order_book()  (companions / legacy)
              ↓
[dashboard/pages/paper_trading.py]
    KPI header + positions table + order counts + 1s autorefresh
              ↓
[Operator browser]
```

### Architecture freeze note

- **LOCKED:** Do not redesign the backend.
- **Do not** modify `paper_trading/paper_trading_runner.py`.
- **Do not** modify broker modules.
- **Do not** place paper or live orders from the dashboard.
- Dashboard pages call **DashboardFacade methods only**.
- Presentation remains free of business logic (formatting / layout / placeholders only).

### Goals

1. Display the six capital/PnL KPIs from facade DTOs.
2. Display the seven-column position table.
3. Display Filled / Pending / Cancelled / Rejected order counts.
4. Refresh the Paper Trading page automatically every **1.0 second**.
5. Soft-degrade to `"—"` / empty tables when paper state is unavailable.
6. Keep types immutable, Google-docstringed, and facade reads thread-safe.

### Success criteria (Definition of Done)

- Paper Trading page reflects Paper Trading Runner state whenever available through the facade.
- Otherwise displays offline placeholders without crashing.
- Autorefresh interval is **1.0 second** for this page only.
- No Paper Trading Runner / broker / strategy source edits required for offline DoD.
- Unit tests cover placeholder path, live mapping, order bucketing, and autorefresh helper.

---

## 2. Responsibilities

| # | Responsibility | Description |
|---|---|---|
| R1 | **Facade paper ledger API** | Expose `get_paper_trading_ledger()` returning immutable DTOs. |
| R2 | **Presentation mapping** | Map ledger DTO into enriched `PaperTradingPageView`. |
| R3 | **Capital KPIs** | Render six capital/PnL cards. |
| R4 | **Position table** | Render Symbol / Strategy / Qty / Entry / Current / MTM / Status. |
| R5 | **Order counts** | Aggregate display counts for Filled / Pending / Cancelled / Rejected. |
| R6 | **Placeholder policy** | Missing values → `"—"`; empty tables when no rows. |
| R7 | **Formatting only** | Format money/qty/status labels; no simulation. |
| R8 | **1s autorefresh** | Re-read facade getters every second; never start trading cycles. |
| R9 | **Thread-safe reads** | Rely on facade `RLock`. |
| R10 | **Documentation** | Google-style docstrings on public API. |

---

## 3. Non-Responsibilities

| # | Non-responsibility | Rationale |
|---|---|---|
| NR1 | **Place / cancel paper orders** | Paper Trading Runner / execution. |
| NR2 | **Modify Paper Trading Runner** | Architecture locked. |
| NR3 | **Call broker APIs** | Broker layer forbidden. |
| NR4 | **Compute strategy signals** | Strategy modules. |
| NR5 | **Invent today's PnL calendar** | Display upstream only; placeholder if absent. |
| NR6 | **Persist ledger to DB** | Out of scope. |

---

## 4. Data Model

### 4.1 `FacadePaperLedgerPosition` (frozen)

| Field | Type | Description |
|---|---|---|
| `symbol` | `str` | Instrument / underlying display |
| `strategy` | `str` | Strategy id/name (or `"—"`) |
| `quantity` | `str` | Qty display |
| `entry` | `str` | Entry / average price |
| `current` | `str` | Current / mark price |
| `mtm` | `str` | Mark-to-market / unrealized PnL |
| `status` | `str` | Position status (e.g. `OPEN` / `CLOSED` / `"—"`) |

### 4.2 `FacadePaperTradingLedger` (frozen)

| Field | Type | Description |
|---|---|---|
| `available_cash` | `str` | Available cash |
| `capital_used` | `str` | Capital / margin used |
| `total_equity` | `str` | Total equity |
| `todays_pnl` | `str` | Today's PnL |
| `realized_pnl` | `str` | Realized PnL |
| `unrealized_pnl` | `str` | Unrealized PnL |
| `positions` | `tuple[FacadePaperLedgerPosition, ...]` | Position rows |
| `orders_filled` | `str` | Count display |
| `orders_pending` | `str` | Count display |
| `orders_cancelled` | `str` | Count display |
| `orders_rejected` | `str` | Count display |
| `orders` | `tuple[FacadeOrderRow, ...]` | Optional detail rows |
| `equity_series` | `tuple[tuple[str, float], ...]` | Optional equity curve |
| `as_of` | `datetime` | Assembly time (UTC) |
| `source` | `str` | `live` / `offline` / `cached` |
| `schema_version` | `str` | `"1.0.0"` |

### 4.3 Presentation `PaperTradingPageView` (extend)

Add the capital/order fields above while keeping legacy `virtual_cash` as an alias of `available_cash` for compatibility.

`PaperPositionView` gains: `strategy`, `entry`, `current`, `mtm`, `status` (legacy `avg_price`/`mark`/`pnl` remain aliases).

### 4.4 Placeholder invariant

**INV-PAPER-001:** Missing numeric fields → `"—"` — never fabricate `0.00` as “no data”.

**INV-PAPER-002:** Offline / no session → all KPIs `"—"`, empty positions, order counts `"0"` or `"—"` (normative: counts `"0"` when known-empty offline book; KPI money fields `"—"`).

Normative offline order counts: `"0"` (explicit empty book). Offline money KPIs: `"—"`.

---

## 5. DashboardFacade API

### 5.1 New method (normative)

```python
def get_paper_trading_ledger(self) -> FacadePaperTradingLedger:
    """Return Paper Trading page ledger snapshot for display.

    Aggregates already-available paper capital, positions, and order
    summaries via the injected session when present; otherwise returns
    offline placeholders. Does not place trades or run simulations.
    """
```

### 5.2 Allowed page reads

| Method | Use |
|---|---|
| `get_paper_trading_ledger()` | Primary Paper Trading page data |
| `get_paper_trading()` | Presentation adapter mapping of the ledger |
| `get_paper_positions()` / `get_order_book()` | Internal companions only |

**Rule FAC-PAPER-001:** Paper Trading page must not call start/stop trading, order placement, or non-facade modules.

**Rule FAC-PAPER-002:** Soft-read session accessors such as `get_paper_trading_ledger`, `get_paper_trading_snapshot`, `get_paper_positions`, `get_order_book`, `get_orders_snapshot` when present.

### 5.3 Upstream mapping (informative)

Capital:

| Display | Upstream candidates |
|---|---|
| Available Cash | `available_cash`, `cash`, `virtual_cash`, `capital.cash` |
| Capital Used | `capital_used`, `reserved_margin_hint`, `used_margin`, `gross_notional` |
| Total Equity | `total_equity`, `equity`, `net_liquidation`; else display-only sum of cash + unrealized when both numeric |
| Today's P&L | `todays_pnl`, `today_pnl`, `daily_pnl` |
| Realized | `realized_pnl`, `total_realized_pnl`, `cumulative_realized_pnl` |
| Unrealized | `unrealized_pnl`, `total_unrealized_pnl` |

Positions:

| Display | Upstream candidates |
|---|---|
| Symbol | `symbol`, `instrument_key` |
| Strategy | `strategy`, `strategy_id` |
| Qty | `quantity`, `qty` |
| Entry | `entry`, `avg_price`, `average_price` |
| Current | `current`, `mark`, `mark_price` |
| MTM | `mtm`, `unrealized_pnl`, `pnl` |
| Status | `status`, `position_status`; default `OPEN` when qty present else `"—"` |

Orders — bucket by normalized status label (display-only):

| Bucket | Status tokens (case-insensitive) |
|---|---|
| Filled | `filled`, `complete`, `completed`, `done` |
| Pending | `pending`, `open`, `submitted`, `new`, `accepted`, `partial` |
| Cancelled | `cancelled`, `canceled` |
| Rejected | `rejected`, `failed`, `expired`, `insufficient_capital` |

Unmatched statuses are ignored for counts (still appear in optional detail list).

### 5.4 Formatting

Money-like numbers → `"{value:,.2f}"` when parseable; otherwise passthrough / placeholder.

---

## 6. UI Specification

### 6.1 `pages/paper_trading.py`

1. Page header: “Paper Trading”.
2. Enable **1s** autorefresh (page scope).
3. Resolve ledger via facade only.
4. Render six KPI cards (Available Cash … Unrealized P&L).
5. Render four order-count badges/cards (Filled / Pending / Cancelled / Rejected).
6. Render Positions table with the seven columns.
7. Optional: equity curve if `equity_series` non-empty (preserve existing chart).
8. Optional: compact recent orders table from `orders` when present.

**Rule UI-PAPER-001:** Catch facade exceptions → error banner + offline placeholders; never crash Streamlit.

### 6.2 Autorefresh

| Setting | Value |
|---|---|
| Interval | `1.0` seconds |
| Scope | Paper Trading page only |
| Action | Re-run → re-call facade getters |
| Trading cycle | **Forbidden** |

Config:

| Field | Default |
|---|---|
| `paper_trading_refresh_seconds` | `1.0` |
| `enable_paper_trading_autorefresh` | `True` |

Validation: `CFG-DASH-PAPER-001` if `paper_trading_refresh_seconds <= 0`.

Mechanism: `streamlit-autorefresh` when available, else `@st.fragment(run_every=...)` wrapping the page body.

---

## 7. Thread Safety & Typing

| Rule | Description |
|---|---|
| TS-001 | Ledger assembly under facade `RLock`. |
| TS-002 | Frozen DTOs; UI session does not own mutable runner state. |
| TY-001 | Full type hints. |
| DOC-001 | Google-style docstrings on public API. |

---

## 8. Error Handling

| Condition | Behavior |
|---|---|
| Offline / no session | Money KPIs `"—"`; order counts `"0"`; empty positions |
| Partial upstream | Map available fields; placeholders for rest |
| Upstream exception | Soft degrade; page still renders |

---

## 9. Files to Touch

| File | Change |
|---|---|
| `docs/specifications/dashboard_paper_trading_integration.md` | This specification |
| `dashboard/dashboard_facade.py` | Ledger DTOs, `get_paper_trading_ledger()`, mapping, empty factory |
| `dashboard/view_models.py` | Enrich paper views + KPI helper |
| `dashboard/pages/paper_trading.py` | UI + 1s refresh |
| `dashboard/config.py` | Paper refresh settings |
| `dashboard/utils/polling.py` | Paper refresh helpers |
| `dashboard/facade.py` | Null facade offline ledger |
| `tests/test_dashboard_paper_trading.py` | Unit/smoke tests |

**Forbidden:** `paper_trading/*`, `broker/*`, `strategy/*` modifications.

---

## 10. Testing Requirements

| ID | Test |
|---|---|
| T01 | Offline ledger: money KPIs `"—"`, order counts `"0"`, empty positions |
| T02 | Live stub maps cash/equity/pnl/position columns |
| T03 | Order statuses bucket into Filled/Pending/Cancelled/Rejected counts |
| T04 | Partial positions map without crash |
| T05 | Presentation `get_paper_trading()` populated from ledger |
| T06 | Page render offline without raise (mocked `st`) |
| T07 | Refresh config defaults to `1.0` |
| T08 | Autorefresh helper true after 1s |
| T09 | No broker / paper_trading_runner imports on page path |

---

## 11. Definition of Done

1. Paper Trading page displays Available Cash, Capital Used, Total Equity, Today's P&L, Realized P&L, Unrealized P&L.
2. Position table shows Symbol, Strategy, Qty, Entry, Current, MTM, Status.
3. Order counts show Filled, Pending, Cancelled, Rejected.
4. Values refresh automatically every second when enabled.
5. Missing state shows graceful placeholders without crashing.
6. All data flows through `DashboardFacade` only.
7. No trading logic; Paper Trading Runner unmodified.
8. Typed; Google docstrings; thread-safe facade reads.

---

## Appendix A — Wireframe

```text
┌───────────┬────────────┬─────────────┬────────────┬────────────┬──────────────┐
│ Avail Cash│ Capital Used│ Total Equity│ Today's P&L│ Realized   │ Unrealized   │
│ 100,000.00│ 25,000.00  │ 100,450.00  │ +450.00    │ +200.00    │ +250.00      │
└───────────┴────────────┴─────────────┴────────────┴────────────┴──────────────┘

┌────────┬─────────┬───────────┬──────────┐
│ Filled │ Pending │ Cancelled │ Rejected │
│ 12     │ 2       │ 1         │ 0        │
└────────┴─────────┴───────────┴──────────┘

Positions
┌────────┬──────────────┬─────┬────────┬─────────┬────────┬────────┐
│ Symbol │ Strategy     │ Qty │ Entry  │ Current │ MTM    │ Status │
├────────┼──────────────┼─────┼────────┼─────────┼────────┼────────┤
│ NIFTY  │ iron_condor  │ -50 │ 120.50 │ 118.00  │ +125.0 │ OPEN   │
└────────┴──────────────┴─────┴────────┴─────────┴────────┴────────┘
        (autorefresh every 1.0s)
```

---

**End of specification — Paper Trading Dashboard Integration v1.0.0**
