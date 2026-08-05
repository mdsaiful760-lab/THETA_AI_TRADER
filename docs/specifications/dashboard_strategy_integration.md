# Strategy Monitor Dashboard Integration — Software Engineering Specification

| Field | Value |
|---|---|
| **Scope** | Strategy Monitor page ↔ `DashboardFacade` ↔ Strategy Framework evaluation state (via session only) |
| **Primary modules** | `dashboard/pages/strategy_monitor.py`, `dashboard/view_models.py`, `dashboard/dashboard_facade.py`, `dashboard/facade.py` |
| **Document version** | 1.0.0 |
| **Status** | Normative — implementation must conform |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-05 |

---

## 1. Purpose

This specification defines the **Strategy Monitor page integration** for THETA AI TRADER v1.0.

The Strategy Monitor page must display the **latest already-computed** strategy evaluation snapshot:

**Header fields**

- Current **Market Regime**
- **Active Strategy**
- **Confidence Score**
- **Strategy Evaluation Time**

**Score table** (always four rows, fixed order)

- Short Strangle
- Iron Condor
- Bull Put Spread
- Bear Call Spread

For each strategy row:

- **Score**
- **Status**
- **Reason**
- **Eligible / Rejected**

All values are consumed **only** through `dashboard/dashboard_facade.py` (`DashboardFacade` / `DashboardIntegrationFacade`). The dashboard **must not** evaluate strategies, score strategies, place orders, call brokers, or invent eligibility decisions.

The feature answers: *"How does the Strategy Monitor page show regime, active strategy, confidence, evaluation time, and the four-strategy score table — using only the Dashboard Facade — without crashing when no evaluation exists?"*

### Pipeline placement

```text
[Strategy Evaluation Engine]  (UNCHANGED — already produces evaluation bundles / reports)
              ↓
[IntegrationSession]  (optional live session; public read accessors only)
              ↓
[dashboard/dashboard_facade.py]     ← ONLY dashboard→backend boundary
    get_strategy_status()
    get_strategy_monitor()  (presentation adapter)
              ↓
[dashboard/pages/strategy_monitor.py]
    KPI header + score table
              ↓
[Operator browser]
```

### Architecture freeze note

- **LOCKED:** Do not redesign the backend.
- **Do not** compute strategy scores in the dashboard.
- **Do not** modify broker modules.
- **Do not** modify strategy modules.
- **Do not** invoke `StrategyEvaluationEngine`, registry plugins, or trading cycles from the dashboard.
- Dashboard pages call **DashboardFacade methods only**.
- Presentation remains free of business logic (formatting / layout / placeholders only).

### Goals

1. Display market regime, active strategy, confidence, and evaluation time from facade DTOs.
2. Always render the four canonical strategies in fixed order.
3. Show Score, Status, Reason, and Eligible/Rejected per row.
4. Soft-degrade to `"—"` placeholders when evaluation is missing or offline.
5. Keep types immutable, methods Google-docstringed, and facade reads thread-safe.
6. Preserve existing dashboard shell / navigation architecture.

### Success criteria (Definition of Done)

- Strategy Monitor shows live evaluation fields whenever backend data is available through the facade.
- Otherwise displays offline placeholders without crashing.
- Short Strangle, Iron Condor, Bull Put Spread, and Bear Call Spread are always present.
- No broker / strategy / execution source edits required for offline DoD.
- Unit tests cover placeholder path, live mapping path, partial fill, and forbidden-import checks.

---

## 2. Responsibilities

| # | Responsibility | Description |
|---|---|---|
| R1 | **Facade strategy status API** | `get_strategy_status() -> FacadeStrategyStatus` returns immutable DTOs. |
| R2 | **Presentation mapping** | `get_strategy_monitor()` / `strategy_status_to_monitor_view()` map facade DTOs to `StrategyMonitorView`. |
| R3 | **Canonical four families** | Always emit the four strategies in `STRATEGY_MONITOR_FAMILIES` order. |
| R4 | **Header KPIs** | Render Market Regime, Active Strategy, Confidence Score, Evaluation Time. |
| R5 | **Score table UI** | Render Strategy / Score / Status / Reason / Eligible·Rejected columns. |
| R6 | **Placeholder policy** | Missing values → configured placeholder (`"—"`); never invent scores. |
| R7 | **Formatting only** | Format numbers/timestamps/labels for display; no ranking or selection math. |
| R8 | **Thread-safe reads** | Rely on facade `RLock`; UI does not mutate shared evaluation state. |
| R9 | **Documentation** | Google-style docstrings on public types/functions. |
| R10 | **Graceful errors** | Catch facade exceptions → error banner + placeholders; never crash Streamlit. |

---

## 3. Non-Responsibilities

| # | Non-responsibility | Rationale |
|---|---|---|
| NR1 | **Evaluate / score strategies** | Strategy Evaluation Engine. |
| NR2 | **Select active strategy** | Trade Decision / orchestrator. |
| NR3 | **Call broker APIs** | Broker layer forbidden in dashboard. |
| NR4 | **Place or cancel orders** | Execution layer. |
| NR5 | **Modify strategy modules** | Architecture locked. |
| NR6 | **Modify broker modules** | Architecture locked. |
| NR7 | **Start trading cycles on page load** | Read-only snapshot re-read only. |
| NR8 | **Persist evaluations** | Out of scope. |

---

## 4. Canonical Strategies

| Display name | Family id |
|---|---|
| Short Strangle | `short_strangle` |
| Iron Condor | `iron_condor` |
| Bull Put Spread | `bull_put_spread` |
| Bear Call Spread | `bear_call_spread` |

**Rule SYM-STRAT-001:** Strategy Monitor **always** renders exactly these four strategies in this order.

**Rule SYM-STRAT-002:** If upstream provides a subset, missing families are filled with placeholder rows.

**Rule SYM-STRAT-003:** Family ids are snake_case; display names are title-cased labels above.

Constant:

```python
STRATEGY_MONITOR_FAMILIES: tuple[tuple[str, str], ...] = (
    ("short_strangle", "Short Strangle"),
    ("iron_condor", "Iron Condor"),
    ("bull_put_spread", "Bull Put Spread"),
    ("bear_call_spread", "Bear Call Spread"),
)
```

---

## 5. Data Model

### 5.1 `FacadeStrategyRow` (facade DTO, frozen)

| Field | Type | Description |
|---|---|---|
| `strategy_id` | `str` | Upstream strategy id or family id fallback |
| `family` | `str` | Canonical family id |
| `display_name` | `str` | Human label (e.g. `Short Strangle`) |
| `status` | `str` | Evaluation status display (or `"—"`) |
| `score` | `str` | Score display (or `"—"`) |
| `eligibility` | `str` | `Eligible` / `Rejected` / `"—"` |
| `reason` | `str` | Primary reason display (or `"—"`) |
| `reasons` | `tuple[str, ...]` | Full reason list for expander |
| `confidence` | `str` | Per-row confidence display (or `"—"`) |
| `last_signal` | `str` | Last signal display (or `"—"`) |
| `timestamp` | `str` | Per-row evaluation timestamp (or `"—"`) |

### 5.2 `FacadeStrategyStatus` (frozen)

| Field | Type | Description |
|---|---|---|
| `strategies` | `tuple[FacadeStrategyRow, ...]` | Length 4, ordered |
| `market_regime` | `str` | Current regime label (or `"—"`) |
| `active_strategy` | `str` | Active / top strategy display (or `"—"`) |
| `confidence_score` | `str` | Header confidence display (or `"—"`) |
| `evaluation_time` | `str` | Evaluation timestamp display (or `"—"`) |
| `as_of` | `datetime` | Snapshot assembly time (tz-aware UTC) |
| `source` | `str` | `live` / `offline` / `cached` |
| `schema_version` | `str` | `"1.0.0"` |

### 5.3 Presentation `StrategyMonitorView` / `StrategyRowView`

Mirror facade header + row fields for Streamlit consumption. `strategy_monitor_kpi_cards(view)` returns the four header KPI cards.

### 5.4 Placeholder invariant

**INV-STRAT-001:** Unknown/missing numeric fields must be `"—"` — never fabricated `0.00` as a substitute for “no evaluation”.

**INV-STRAT-002:** Offline / disconnected / missing snapshot → four placeholder rows and placeholder header fields.

---

## 6. DashboardFacade API

### 6.1 Normative methods

```python
def get_strategy_status(self) -> FacadeStrategyStatus:
    """Return the Strategy Monitor evaluation snapshot for display.

    Aggregates already-available upstream evaluation fields via the
    injected session when present; otherwise returns offline placeholders.
    Does not evaluate strategies, select strategies, or start trading cycles.
    """

def get_strategy_monitor(self) -> StrategyMonitorView:
    """Presentation adapter: map get_strategy_status() to StrategyMonitorView."""
```

### 6.2 Allowed Strategy Monitor page reads

| Method | Use |
|---|---|
| `get_strategy_monitor()` | Preferred page entry |
| `get_strategy_status()` | Allowed if presentation adapter unavailable |

**Rule FAC-STRAT-001:** Strategy Monitor must not call `get_portfolio`, order APIs, broker clients, or any non-facade module.

**Rule FAC-STRAT-002:** Implementation may soft-read session accessors such as `get_strategy_status`, `get_strategy_evaluation_summary`, and optional `get_market_regime` / `get_regime_snapshot` when present — soft-degrade when absent.

### 6.3 Upstream mapping (informative)

Prefer, in order:

1. `session.get_strategy_status()` if available.
2. `session.get_strategy_evaluation_summary()` (bundle / reports / rows).
3. Else offline placeholders.

Row field preferences (display mapping only):

| Display field | Upstream candidates |
|---|---|
| Score | `score`, `ranking_score`, `suitability_score` |
| Status | `status`, `evaluation_status` |
| Reason | `reason`, first of `reasons` / `reason_codes` |
| Eligibility | `eligibility`, `eligible` / `is_eligible`, else display map of known status/outcome labels |
| Family | `family`, `strategy_family`, normalized `display_name` |

Header field preferences:

| Display field | Upstream candidates |
|---|---|
| Market regime | `market_regime`, `regime`, optional regime snapshot |
| Active strategy | `active_strategy`, `selected_strategy`, `top_strategy_id`, summary top id |
| Confidence | `confidence_score`, `confidence`, top-row confidence |
| Evaluation time | `evaluation_time`, `evaluated_at`, `timestamp` |

**Eligibility display rule:** Map already-computed upstream flags/labels to `Eligible` / `Rejected`. Do **not** re-score or re-decide eligibility in the dashboard.

### 6.4 Formatting rules

| Field | Format example |
|---|---|
| Score | `"88.25"` or `"—"` |
| Confidence | `"77.0%"` / `"0.77"` display via formatter, or `"—"` |
| Evaluation time | `"2026-08-05 12:00:00 UTC"` or `"—"` |
| Eligibility | `"Eligible"` / `"Rejected"` / `"—"` |

---

## 7. UI Specification

### 7.1 `pages/strategy_monitor.py`

1. `render_page_header("Strategy Monitor", ...)`.
2. Resolve snapshot via facade only (`get_strategy_monitor` preferred).
3. `render_kpi_row(strategy_monitor_kpi_cards(snapshot))`.
4. Render score table with columns:
   - Strategy
   - Score
   - Status
   - Reason
   - Eligible / Rejected
5. Optional expander for full reason lists when present.
6. If all scores/statuses are placeholders → info: awaiting backend evaluations.

**Rule UI-STRAT-001:** Catch facade exceptions → show error banner + placeholder snapshot; never raise to crash Streamlit.

### 7.2 KPI cards

| Label | Source |
|---|---|
| Market Regime | `StrategyMonitorView.market_regime` |
| Active Strategy | `StrategyMonitorView.active_strategy` |
| Confidence Score | `StrategyMonitorView.confidence_score` |
| Strategy Evaluation Time | `StrategyMonitorView.evaluation_time` |

---

## 8. Thread Safety & Typing

| Rule | Description |
|---|---|
| TS-001 | All facade strategy status assembly under existing facade `RLock`. |
| TS-002 | DTOs frozen; Streamlit session stores only page id / UI prefs. |
| TY-001 | Full type hints on public functions and dataclasses. |
| DOC-001 | Google-style docstrings on public API. |

---

## 9. Error Handling

| Condition | Behavior |
|---|---|
| Facade offline / no session | Four placeholder rows; header `"—"` |
| Partial upstream rows | Fill available families; placeholders for rest |
| Upstream exception | Soft degrade via facade; page still renders |
| Page-level exception | Error banner + offline placeholders |

Error codes (facade / presentation):

| Code | Meaning |
|---|---|
| `DIF.UPSTREAM.ERROR` | Existing upstream error path |
| `DIF.UPSTREAM.UNSUPPORTED` | Optional accessor missing (when recorded) |

---

## 10. Files to Touch

| File | Change |
|---|---|
| `dashboard/dashboard_facade.py` | `STRATEGY_MONITOR_FAMILIES`, enriched `FacadeStrategyRow` / `FacadeStrategyStatus`, `empty_strategy_status`, `strategy_status_to_monitor_view`, `_fetch_strategy_status` |
| `dashboard/view_models.py` | Enrich `StrategyRowView` / `StrategyMonitorView`; `strategy_monitor_kpi_cards` |
| `dashboard/pages/strategy_monitor.py` | KPI header + score table wiring |
| `dashboard/facade.py` | Null facade returns four-family placeholders |
| `tests/test_dashboard_strategy_monitor.py` | Unit/smoke tests |

**Forbidden to modify for this feature:** `broker/*`, `strategy/*`, execution / orchestrator internals (consume existing session read APIs only).

---

## 11. Testing Requirements

| ID | Test |
|---|---|
| T01 | Offline facade returns 4 strategies with `"—"` fields |
| T02 | Family order is Short Strangle → Iron Condor → Bull Put Spread → Bear Call Spread |
| T03 | Live stub maps score/status/reason/eligibility + header fields |
| T04 | Partial upstream fills missing families with placeholders |
| T05 | Presentation adapter populates `StrategyMonitorView` |
| T06 | Page render does not raise on placeholders (mocked `st`) |
| T07 | No broker imports on Strategy Monitor / facade path |
| T08 | No strategy-evaluation engine imports from dashboard page |

Coverage: extend facade tests for four-family invariant; smoke-test page render.

---

## 12. Definition of Done

Complete when all are true:

1. Strategy Monitor displays Market Regime, Active Strategy, Confidence Score, and Evaluation Time.
2. Score table always shows the four canonical strategies with Score, Status, Reason, Eligible/Rejected.
3. Unavailable evaluations show graceful placeholders without crashing.
4. All data flows through `DashboardFacade` only.
5. No strategy computation; no broker; no execution logic in dashboard.
6. Fully typed; Google docstrings; thread-safe facade reads.
7. Backend architecture remains locked and unmodified beyond optional consumption of existing session read APIs.

---

## Appendix A — Wireframe

```text
┌──────────────┬────────────────┬─────────────────┬──────────────────────────┐
│ Market Regime│ Active Strategy│ Confidence Score│ Strategy Evaluation Time │
│ RANGE_BOUND  │ Iron Condor    │ 77.0%           │ 2026-08-05 12:00:00 UTC  │
└──────────────┴────────────────┴─────────────────┴──────────────────────────┘

┌──────────────────┬────────┬──────────┬──────────────────┬───────────────────┐
│ Strategy         │ Score  │ Status   │ Reason           │ Eligible/Rejected │
├──────────────────┼────────┼──────────┼──────────────────┼───────────────────┤
│ Short Strangle   │ 55.00  │ abstain  │ pop_low          │ Rejected          │
│ Iron Condor      │ 88.25  │ success  │ regime_fit       │ Eligible          │
│ Bull Put Spread  │ —      │ —        │ —                │ —                 │
│ Bear Call Spread │ 12.00  │ failed   │ no_candidates    │ Rejected          │
└──────────────────┴────────┴──────────┴──────────────────┴───────────────────┘
```

---

## Appendix B — Out of Scope

- Live strategy execution controls
- Strategy parameter editing
- Order ticket from Strategy Monitor
- Auto-selecting trades from the score table
- Computing Greeks / IV / regime inside the dashboard

---

**End of specification — Strategy Monitor Dashboard Integration v1.0.0**
