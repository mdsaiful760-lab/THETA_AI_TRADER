# Dashboard Foundation — Software Engineering Specification

| Field | Value |
|---|---|
| **Module** | `dashboard/app.py` (+ `dashboard/` package) |
| **Document version** | 1.0.0 |
| **Status** | Draft — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-05 |

---

## 1. Purpose

`dashboard/app.py` and the surrounding `dashboard/` package define the **institutional Streamlit presentation layer** for THETA AI TRADER v1.0.

The dashboard is a **read-mostly operator console** that renders system health, market context placeholders, strategy summaries, paper trading ledgers, orders, portfolio, risk, APME decisions, logs, analytics, and settings — consuming **already-computed** artifacts from the frozen backend through `IntegrationSession` and immutable view models.

The module answers: *"How do we present THETA AI TRADER state to an operator in a professional multi-page Streamlit UI — without embedding trading logic, broker logic, or database logic in the presentation layer?"*

It is **not** a trading engine. It is **not** a strategy evaluator. It is **not** a risk calculator. It is **not** a broker client. It is **not** a database. It is the **presentation gate** between human operators and the Integration Engine / System Orchestrator facade.

### Pipeline placement

```text
[Backend — FROZEN INSTITUTIONAL PIPELINE]
    Market Data → Strategy Evaluation → Trade Decision
    → Risk → Execution → Order / Paper Runner
    → Position → Portfolio → APME
              ↓
[system/integration_engine.py]
    IntegrationSession (public facade)
    get_health() · get_runtime_state() · start()/stop()
    (optional read-only snapshot accessors)
              ↓
[dashboard/app.py]                         ← THIS PACKAGE (PRESENTATION ONLY)
    Streamlit multipage shell
    Sidebar controls (nav · status · start/stop/refresh)
    Page modules render view models
              ↓
[Operator browser]
```

### Architecture freeze note

The platform architecture is **FROZEN** for v1.0:

- Dashboard **must not** redesign or bypass the backend pipeline.
- Dashboard **must not** create trading logic, strategy logic, risk math, order placement, or APME rules.
- Dashboard **must not** import Kite SDK, broker adapters for mutation, or invent parallel ledgers.
- All start/stop/refresh actions **delegate** to `IntegrationSession` / System Orchestrator public APIs.
- All displayed metrics come from **immutable snapshots / redacted config / event-derived view models**.
- Pages are **independent modules**; shared UI lives in `components/`; shared helpers in `utils/`.
- No new analytical engines are introduced by this package.

### Goals

1. Provide a **professional Streamlit multipage dashboard** that starts successfully and navigates cleanly.
2. Implement **eleven independent pages** with consistent dark theme and responsive layout.
3. Provide a **sidebar** with navigation, system/broker/market/execution status, and Start / Stop / Refresh controls.
4. Render **Home (Trading Terminal)** with index placeholders, KPI cards, and a TradingView chart placeholder.
5. Remain a **pure presentation layer** — zero business logic beyond formatting, layout, and safe session-state UI plumbing.
6. Consume backend state via **injected/read-only facades** (`IntegrationSession`, view adapters) — never construct engines.
7. Support **versioned dashboard configuration** aligned with `DashboardConfiguration` from Application Configuration.
8. Be **future-ready** for live updates (polling / event-bus bridging) without changing page contracts.
9. Use **fully typed Python**, Google-style docstrings, and thread-safe session access patterns.
10. Reserve **TradingView Lightweight Charts** integration behind a clear placeholder contract; use **Plotly** for portfolio/equity charts in v1.
11. Keep pages modular so each page can be developed, tested, and reviewed independently.
12. Achieve a **Definition of Done** where the app starts, navigation works, all pages render, and UI is professional.

### Success criteria

- `streamlit run dashboard/app.py` (or documented entry) starts without import errors.
- Sidebar navigation reaches all eleven pages.
- Every page renders a professional layout (placeholder content allowed where backend snapshots are absent).
- Start / Stop / Refresh buttons call facade methods only — no inline trading logic.
- No module under `dashboard/` imports broker SDKs, risk internals, strategy plugin evaluation, or DB ORMs for persistence.
- Dark theme applied globally via Streamlit theming / custom CSS assets.
- Unit/smoke tests cover page registry, config loading, view-model adapters, and “no forbidden imports” guard.
- Configuration schema version `1.0.0` for dashboard-local UI settings.

### Relationship to other modules

| Module | Relationship |
|---|---|
| `system/integration_engine.py` | **Primary backend facade.** Dashboard reads `IntegrationSession` health/runtime; delegates start/stop. |
| `system/system_orchestrator.py` | **Indirect.** Invoked only through Integration Session. |
| `config/application_configuration.py` | **Config source.** Consumes `DashboardConfiguration` + redacted app config. |
| `paper_trading/paper_trading_runner.py` | **Read-only consumer.** Displays paper capital/positions/PnL snapshots via facade. |
| `portfolio/portfolio_manager.py` | **Read-only.** Portfolio page displays snapshots — never mutates. |
| `portfolio/position_manager.py` | **Read-only.** Open positions cards/tables. |
| `execution/order_manager.py` | **Read-only.** Orders page displays trackers/events. |
| `risk/risk_engine.py` | **Read-only.** Risk page displays last verdict summaries. |
| `apme/adaptive_position_management_engine.py` | **Read-only.** APME page displays decision reports. |
| `core/event_bus.py` | **Optional live bridge (future).** Dashboard may subscribe via a thread-safe buffer — never publish trading commands as domain logic. |
| Broker / Kite SDK | **Forbidden.** Status only via Integration Session health. |

### Distinction from Integration Engine

| Concern | Integration Engine | Dashboard |
|---|---|---|
| Role | Composition root / process facade | Presentation / operator UI |
| Constructs engines | Yes | **Never** |
| Renders UI | **Never** | Yes |
| Trading cycles | Delegates to orchestrator | May trigger start/stop/refresh via facade only |

### Distinction from backend engines

| Concern | Backend engines | Dashboard |
|---|---|---|
| Domain intelligence | Core responsibility | **Forbidden** |
| Immutable artifacts | Produce | Display |
| Fail-closed trading | Yes | N/A — display errors only |

---

## 2. Responsibilities

The `dashboard/` package **is responsible for**:

| # | Responsibility | Description |
|---|---|---|
| R1 | **Streamlit application entry** | Provide `dashboard/app.py` as the multipage shell entrypoint. |
| R2 | **Page registry** | Register and route eleven independent pages. |
| R3 | **Sidebar chrome** | Render navigation, status indicators, Start/Stop/Refresh controls. |
| R4 | **Dark theme & layout** | Apply professional dark theme, spacing, and responsive columns. |
| R5 | **Home trading terminal** | Show index placeholders, KPI cards, chart placeholder. |
| R6 | **View-model adaptation** | Map backend snapshots → display DTOs (formatting only). |
| R7 | **Facade delegation** | Wire Start/Stop/Refresh to `IntegrationSession` public methods. |
| R8 | **Placeholder content** | Render explicit placeholders when data unavailable (never invent metrics). |
| R9 | **Plotly chart helpers** | Provide portfolio/equity chart builders from tabular series. |
| R10 | **TradingView placeholder** | Reserve chart container + future JS component contract. |
| R11 | **Session state management** | Thread-safe Streamlit session keys for UI-only state. |
| R12 | **Versioned UI configuration** | Load `DashboardUiConfig` schema `1.0.0`. |
| R13 | **Asset loading** | CSS / logos under `dashboard/assets/`. |
| R14 | **Logging display** | Logs page tails redacted log lines / event summaries. |
| R15 | **Settings display** | Show redacted configuration; never show secrets. |
| R16 | **Smoke-testability** | Pages importable and renderable under Streamlit testing harness. |
| R17 | **Documentation contract** | Google-style docstrings on public modules/functions/classes. |
| R18 | **Future live updates** | Define polling interval hooks compatible with `refresh_interval_seconds`. |
| R19 | **Error banners** | Display facade errors without crashing the shell. |
| R20 | **Accessibility basics** | Labels, contrast-friendly dark theme tokens. |

---

## 3. Non-Responsibilities

The `dashboard/` package **must not**:

| # | Non-responsibility | Rationale |
|---|---|---|
| NR1 | **Evaluate strategies or score signals** | Strategy Evaluation / Scoring engines. |
| NR2 | **Calculate indicators, Greeks, IV, or regime** | Market intelligence engines. |
| NR3 | **Calculate risk scores or emit RiskVerdict** | Risk Engine. |
| NR4 | **Build or modify ExecutionPlan** | Execution Engine. |
| NR5 | **Place, modify, or cancel broker orders** | Order Manager / broker. |
| NR6 | **Simulate fills or maintain paper ledgers** | Paper Trading Runner. |
| NR7 | **Mutate positions or portfolio** | Position / Portfolio Managers. |
| NR8 | **Run APME decision logic** | APME. |
| NR9 | **Connect to broker APIs or import Kite SDK** | Broker layer / Integration Engine. |
| NR10 | **Own database schemas or persistence** | External persistence / future services. |
| NR11 | **Construct engines or SystemOrchestrator** | Integration Engine only. |
| NR12 | **Load raw `.env` secrets into UI** | Config + secret refs; display redacted only. |
| NR13 | **Invent live prices when marks missing** | Show placeholder / “N/A”. |
| NR14 | **Bypass IntegrationSession for start/stop** | All lifecycle via facade. |
| NR15 | **Embed trading algorithms in callbacks** | Presentation only. |
| NR16 | **Redesign backend architecture** | Architecture locked. |
| NR17 | **Create new analytical engines** | Forbidden. |
| NR18 | **Store operator credentials in session unencrypted** | Auth policy via config; v1 local-only. |

---

## 4. Package Structure

```text
dashboard/
├── __init__.py                 # Package exports / version
├── app.py                      # Streamlit entry — shell + sidebar + router
├── config.py                   # DashboardUiConfig (versioned, frozen)
├── theme.py                    # Dark theme tokens + CSS injection
├── session_state.py            # Typed session keys + thread-safe helpers
├── facade.py                   # IntegrationSession adapter (read-only + lifecycle)
├── view_models.py              # Immutable display DTOs
├── components/
│   ├── __init__.py
│   ├── sidebar.py              # Navigation + status + control buttons
│   ├── status_badges.py        # System / broker / market / mode badges
│   ├── kpi_cards.py            # Metric cards row
│   ├── index_ticker.py         # NIFTY / BANKNIFTY / SENSEX / INDIA VIX placeholders
│   ├── chart_placeholder.py    # TradingView Lightweight Charts placeholder
│   ├── plotly_charts.py        # Equity / portfolio Plotly builders
│   ├── data_table.py           # Styled dataframe tables
│   ├── page_header.py          # Consistent page titles
│   └── error_banner.py         # Error / warning banners
├── pages/
│   ├── __init__.py             # PAGE_REGISTRY
│   ├── home.py                 # 1. Trading Terminal
│   ├── market.py               # 2. Market
│   ├── strategy_monitor.py     # 3. Strategy Monitor
│   ├── paper_trading.py        # 4. Paper Trading
│   ├── orders.py               # 5. Orders
│   ├── portfolio.py            # 6. Portfolio
│   ├── risk.py                 # 7. Risk
│   ├── apme.py                 # 8. APME
│   ├── logs.py                 # 9. Logs
│   ├── analytics.py            # 10. Analytics
│   └── settings.py             # 11. Settings
├── assets/
│   ├── theme.css               # Dark theme overrides
│   └── logo.svg                # Optional brand mark
└── utils/
    ├── __init__.py
    ├── formatting.py           # Money / pct / timestamp formatters
    ├── guards.py               # Forbidden-import / runtime guards
    └── polling.py              # Refresh interval helpers (future-ready)
```

**Rule PKG-001:** Every page module exposes `render(ctx: DashboardRenderContext) -> None`.

**Rule PKG-002:** Pages must not import sibling pages.

**Rule PKG-003:** Components must not call Integration Session lifecycle methods except via injected callbacks from `app.py` / `sidebar.py`.

**Rule PKG-004:** `assets/` contains only static presentation resources.

---

## 5. Technology Stack

| Technology | Role in v1.0 |
|---|---|
| **Streamlit** | Application shell, multipage navigation, widgets |
| **Pandas** | Tabular display frames (conversion from view models only) |
| **Plotly** | Portfolio equity, drawdown, allocation charts |
| **TradingView Lightweight Charts** | **Placeholder only** in v1 — reserved container + contract for future `components.html` / custom component |
| **Python 3.9+** | Typed modules, frozen dataclasses |

**Dependency rule DEP-001:** Dashboard may depend on Streamlit, Plotly, Pandas, and backend **public** facade types. It must not depend on broker SDKs or engine internals.

**Optional:** `streamlit-autorefresh` or custom `st_autorefresh` shim for polling — behind feature flag.

---

## 6. Application Entry (`dashboard/app.py`)

### 6.1 Responsibilities of `app.py`

1. Configure Streamlit page (`page_title`, `page_icon`, `layout="wide"`, initial sidebar state).
2. Inject dark theme CSS from `assets/theme.css`.
3. Initialize typed session state.
4. Resolve `DashboardUiConfig` + optional `IntegrationSession` (or demo/offline facade).
5. Render sidebar via `components.sidebar`.
6. Dispatch to selected page `render(ctx)`.
7. Catch presentation-level exceptions and show `error_banner` without killing the process.

### 6.2 Entry sketch (normative intent)

```python
def main() -> None:
    """Run the THETA AI TRADER Streamlit dashboard shell."""
    configure_page()
    apply_theme()
    ensure_session_state()
    ctx = build_render_context()
    render_sidebar(ctx)
    page = resolve_page(ctx.session.active_page)
    page.render(ctx)


if __name__ == "__main__":
    main()
```

### 6.3 Offline / demo mode

When Integration Session is unavailable (local UI development):

- Use `NullIntegrationFacade` returning empty snapshots and `status=DISCONNECTED`.
- All pages must still render placeholders.
- Start/Stop buttons disabled with tooltip “Backend session not connected”.

**Rule APP-001:** Demo mode must never fabricate PnL, prices, or risk scores — only empty/placeholder strings.

---

## 7. Layout & Theme

### 7.1 Visual direction

- **Dark theme** default (near-black surfaces, muted borders, high-contrast text).
- Professional institutional terminal aesthetic — dense but readable; avoid playful/generic marketing layouts.
- Consistent spacing scale; KPI cards in a single row on desktop, wrapping on mobile.
- CSS variables in `assets/theme.css`:

| Token | Example | Usage |
|---|---|---|
| `--theta-bg` | `#0B0F14` | App background |
| `--theta-surface` | `#121821` | Cards / panels |
| `--theta-border` | `#1E2A38` | Dividers |
| `--theta-text` | `#E8EEF5` | Primary text |
| `--theta-muted` | `#8B9BB0` | Secondary text |
| `--theta-accent` | `#3D8BFF` | Links / active nav |
| `--theta-positive` | `#2ECC71` | Gains |
| `--theta-negative` | `#E74C3C` | Losses |
| `--theta-warning` | `#F0B429` | Caution |

### 7.2 Responsive layout

- Use Streamlit columns with breakpoints via relative widths.
- Sidebar collapsible (`initial_sidebar_state="expanded"` desktop).
- Charts use `use_container_width=True`.
- Tables scroll horizontally when needed.

### 7.3 Typography

- Prefer Streamlit + CSS stack; avoid default “AI purple gradient” aesthetics.
- Page headers via `page_header` component: title + one-line subtitle.

---

## 8. Sidebar Specification

### 8.1 Sections (top → bottom)

1. **Brand** — “THETA AI TRADER” + version badge (`DASHBOARD_VERSION`).
2. **Navigation** — radio/select for eleven pages (see §9).
3. **System Status** — badge from facade health (`RUNNING` / `STOPPED` / `DEGRADED` / `UNKNOWN`).
4. **Broker Status** — `CONNECTED` / `DISCONNECTED` / `N/A` (paper).
5. **Execution Mode** — `PAPER` / `LIVE` / `ANALYSIS` / `BACKTEST` (display only from runtime state).
6. **Market Status** — `OPEN` / `CLOSED` / `UNKNOWN` placeholder from facade or clock heuristic display only (no trading gate logic).
7. **Controls**
   - **Start** → `facade.start()`
   - **Stop** → `facade.stop()`
   - **Refresh** → `facade.refresh_snapshots()` + `st.rerun()`
8. **Footer** — last refresh timestamp (timezone-aware display).

### 8.2 Control rules

| Rule | Description |
|---|---|
| CTRL-001 | Buttons invoke facade only; no engine construction. |
| CTRL-002 | Disable Start when already RUNNING. |
| CTRL-003 | Disable Stop when STOPPED / DISCONNECTED. |
| CTRL-004 | Refresh always enabled; shows spinner while pending. |
| CTRL-005 | Failures surface via `error_banner`, not exceptions to user. |

### 8.3 Navigation labels

| Page ID | Label |
|---|---|
| `home` | Home |
| `market` | Market |
| `strategy_monitor` | Strategy Monitor |
| `paper_trading` | Paper Trading |
| `orders` | Orders |
| `portfolio` | Portfolio |
| `risk` | Risk |
| `apme` | APME |
| `logs` | Logs |
| `analytics` | Analytics |
| `settings` | Settings |

---

## 9. Pages

Every page implements:

```python
def render(ctx: DashboardRenderContext) -> None:
    """Render this page.

    Args:
        ctx: Immutable render context with facade + session handles.
    """
```

Pages **must** call `page_header` first, then layout sections. Missing data → explicit placeholders (`"—"` / “Awaiting backend”).

### 9.1 Home (Trading Terminal) — `pages/home.py`

**Purpose:** Primary operator terminal.

**Layout:**

1. **Index strip** (`index_ticker`): placeholders for
   - NIFTY
   - BANKNIFTY
   - SENSEX
   - INDIA VIX  
   Each shows label + `value` or `—` + optional change stub.
2. **KPI cards** (`kpi_cards`):
   - Active Strategy
   - Confidence
   - Market Regime
   - Paper PnL
   - Open Positions
3. **Center chart** (`chart_placeholder`): TradingView Lightweight Charts reserved region with caption “Chart integration pending — TradingView Lightweight Charts”.
4. Optional bottom row: last cycle summary (correlation id, status) if facade provides it.

**Data source:** `DashboardSnapshot.home` view model — never computed locally.

### 9.2 Market — `pages/market.py`

- Underlying selector (display list from config/facade).
- Placeholder quote panel (LTP, change, volume) — N/A when absent.
- Option chain table placeholder (empty Pandas frame with columns).
- No strike selection trading actions in v1 (display only).

### 9.3 Strategy Monitor — `pages/strategy_monitor.py`

- Table of registered strategies / last evaluation summaries from facade.
- Columns: strategy_id, family, status, confidence, last_signal, timestamp.
- Detail expander for reasons/factors (strings from backend).

### 9.4 Paper Trading — `pages/paper_trading.py`

- Virtual cash, realized/unrealized PnL cards.
- Paper positions table.
- Plotly equity curve if series provided; else placeholder chart.
- No simulate/fill buttons that call runner internals directly — refresh only via facade.

### 9.5 Orders — `pages/orders.py`

- Recent `OrderTracker` / paper order summaries as table.
- Filters: status, plan_id (UI filter on already-fetched rows only).
- No place/cancel order widgets in v1 foundation (explicit note: “Order mutation not available in dashboard v1”).

### 9.6 Portfolio — `pages/portfolio.py`

- Portfolio snapshot metrics (equity, exposure, utilization) from facade.
- Positions table.
- Plotly allocation / equity charts via `plotly_charts`.

### 9.7 Risk — `pages/risk.py`

- Last risk verdict card (APPROVED / REJECTED / SKIPPED).
- Reason codes list.
- Limits display from redacted config (read-only).
- No “override risk” controls in v1.

### 9.8 APME — `pages/apme.py`

- Latest APME decision report summaries.
- Per-position management hints table.
- Explicit banner: “APME decisions are informational; execution remains orchestrator-owned.”

### 9.9 Logs — `pages/logs.py`

- Scrollable log/event list from facade ring buffer or file tail adapter (read-only).
- Level filter (INFO/WARN/ERROR) — client-side filter.
- Never display secrets; redaction helper mandatory.

### 9.10 Analytics — `pages/analytics.py`

- Placeholder analytics panels: win rate, expectancy, regime histogram — **only if** backend supplies aggregates.
- Plotly charts for provided series.
- Empty-state copy when analytics service not wired.

### 9.11 Settings — `pages/settings.py`

- Redacted `ApplicationConfiguration` / `DashboardConfiguration` display.
- UI preferences: refresh interval (clamped), default page, theme (dark fixed in v1).
- Persist UI prefs in Streamlit session / local optional JSON under `data/` **only for UI prefs** — never trading state DB.
- No secret editing fields.

---

## 10. Data Model (Presentation DTOs)

All display DTOs are **immutable** (`frozen=True`) and contain only presentation fields.

### 10.1 `DashboardRenderContext`

| Field | Type | Description |
|---|---|---|
| `config` | `DashboardUiConfig` | Versioned UI config |
| `facade` | `DashboardBackendFacade` | Protocol for backend access |
| `session` | `DashboardSessionView` | UI session snapshot |
| `clock` | `Callable[[], datetime]` | Injected clock for tests |
| `version` | `str` | `DASHBOARD_VERSION` |

### 10.2 `DashboardUiConfig` (schema `1.0.0`)

| Field | Type | Default | Constraints |
|---|---|---|---|
| `schema_version` | `str` | `"1.0.0"` | Must equal `DASHBOARD_UI_SCHEMA_VERSION` |
| `app_title` | `str` | `"THETA AI TRADER"` | Non-empty |
| `default_page` | `str` | `"home"` | Must be valid page id |
| `refresh_interval_seconds` | `float` | from `DashboardConfiguration` or `2.0` | `> 0` |
| `enable_autorefresh` | `bool` | `False` | Future live updates |
| `show_demo_banners` | `bool` | `True` when facade offline | — |
| `plotly_template` | `str` | `"plotly_dark"` | — |
| `index_symbols` | `tuple[str, ...]` | `("NIFTY","BANKNIFTY","SENSEX","INDIA VIX")` | Non-empty |
| `metadata` | `Mapping[str, str]` | `{}` | — |

Construction raises `DashboardConfigurationError` with codes `CFG-DASH-*`.

### 10.3 Home view models

```text
IndexQuoteView(symbol, value: str, change: str, status: str)
HomeKpiView(active_strategy, confidence, market_regime, paper_pnl, open_positions)
HomePageView(indices: tuple[IndexQuoteView, ...], kpis: HomeKpiView, cycle_summary: str | None)
```

Missing backend fields → string `"—"` (em dash), never `None` in rendered text paths.

### 10.4 Facade protocol

```python
class DashboardBackendFacade(Protocol):
    def get_health(self) -> SystemStatusView: ...
    def get_runtime_state(self) -> RuntimeStateView: ...
    def get_home_snapshot(self) -> HomePageView: ...
    def get_market_snapshot(self) -> MarketPageView: ...
    def get_strategy_monitor(self) -> StrategyMonitorView: ...
    def get_paper_trading(self) -> PaperTradingPageView: ...
    def get_orders(self) -> OrdersPageView: ...
    def get_portfolio(self) -> PortfolioPageView: ...
    def get_risk(self) -> RiskPageView: ...
    def get_apme(self) -> ApmePageView: ...
    def get_logs(self, *, limit: int = 200) -> LogsPageView: ...
    def get_analytics(self) -> AnalyticsPageView: ...
    def get_settings_view(self) -> SettingsPageView: ...
    def start(self) -> FacadeActionResult: ...
    def stop(self) -> FacadeActionResult: ...
    def refresh_snapshots(self) -> FacadeActionResult: ...
```

**Rule FAC-001:** Implementations may wrap `IntegrationSession`; they must not reimplement engine logic.

**Rule FAC-002:** All `get_*` methods are read-only and thread-safe.

**Rule FAC-003:** `NullIntegrationFacade` implements the protocol for offline UI.

---

## 11. Session State & Thread Safety

### 11.1 Typed session keys

| Key | Type | Purpose |
|---|---|---|
| `active_page` | `str` | Current page id |
| `last_error` | `str \| None` | Banner message |
| `last_refresh_at` | `str \| None` | ISO timestamp |
| `facade_action_pending` | `bool` | Disable double-clicks |
| `ui_prefs` | `Mapping` | Non-secret UI prefs |

### 11.2 Thread-safety rules

| Rule | Description |
|---|---|
| TS-001 | All writes to shared facade caches guarded by `threading.RLock` inside facade adapter. |
| TS-002 | Streamlit script runs are single-threaded per session; do not spawn unmanaged threads for trading. |
| TS-003 | Optional background poller (future) must only push immutable snapshots into a thread-safe queue consumed on rerun. |
| TS-004 | Never mutate backend dataclasses; copy/adapt to view models. |

### 11.3 Determinism for tests

Inject `clock` and `NullIntegrationFacade` with fixed snapshots so page render smoke tests are deterministic.

---

## 12. Components

### 12.1 `components/sidebar.py`

`render_sidebar(ctx) -> None` — builds navigation + status + buttons.

### 12.2 `components/kpi_cards.py`

`render_kpi_row(cards: Sequence[KpiCardModel]) -> None` — five equal columns on wide layout.

### 12.3 `components/index_ticker.py`

`render_index_strip(indices: Sequence[IndexQuoteView]) -> None`.

### 12.4 `components/chart_placeholder.py`

`render_tradingview_placeholder(*, height: int = 420) -> None`

- Dark panel with border
- Centered label explaining future TradingView Lightweight Charts integration
- Optional `st.components.v1.html` stub with empty container `div#theta-tv-chart` for future JS

### 12.5 `components/plotly_charts.py`

| Function | Purpose |
|---|---|
| `build_equity_curve(df: pd.DataFrame) -> go.Figure` | Equity over time |
| `build_allocation_pie(df: pd.DataFrame) -> go.Figure` | Allocation |
| `build_drawdown(df: pd.DataFrame) -> go.Figure` | Drawdown |

All figures use dark template; no analytics computation beyond plotting provided columns.

### 12.6 `components/data_table.py`

`render_table(df: pd.DataFrame, *, height: int | None = None) -> None` — `st.dataframe` with consistent config.

### 12.7 `components/error_banner.py`

`render_error(message: str | None) -> None` / `render_warning(...)`.

---

## 13. Backend Integration Contract

### 13.1 Preferred wiring

```text
Integration Engine bootstrap (CLI or dashboard bootstrap helper)
    → IntegrationSession
    → IntegrationSessionFacade(session) implementing DashboardBackendFacade
    → dashboard/app.py
```

Dashboard process **must not** call `load_application_configuration` to construct engines; a thin `dashboard/bootstrap.py` (optional) may obtain an already-built session from Integration Engine public helpers such as `create_paper_trading_session()` / `create_development_session()` **without** duplicating wiring logic.

### 13.2 Start / Stop / Refresh semantics

| Action | Facade behavior |
|---|---|
| Start | `session.start()` or orchestrator start equivalent |
| Stop | `session.stop()` graceful |
| Refresh | Re-read health + snapshot getters; no new trading cycle unless facade explicitly documents `request_cycle()` (v1 Refresh = snapshot refresh only) |

**Rule LIFE-001:** v1 Refresh does **not** force a trading cycle by default (avoids accidental trading from UI spam). Optional “Run Cycle” control is **out of scope** for foundation DoD (may appear later behind feature flag).

### 13.3 Forbidden imports (static guard)

`utils/guards.py` provides `assert_no_forbidden_dashboard_imports(module_ast)` used in tests.

Forbidden module prefixes include:

- `kiteconnect`
- `broker.zerodha`
- `risk.risk_engine` (direct) — allow only facade/view imports of public result types if needed for typing
- Prefer: dashboard imports **only** `dashboard.*`, `pandas`, `plotly`, `streamlit`, and `system.integration_engine` / facade protocols / immutable DTO modules explicitly allowlisted.

**Practical allowlist (v1):**

- `dashboard.*`
- `streamlit`, `pandas`, `plotly`
- `system.integration_engine` (session types only)
- `config.application_configuration` (DashboardConfiguration / redacted views)
- Typing/stdlib

Engine modules may be referenced **only** for type-checking of immutable result types via `TYPE_CHECKING` if required — runtime page code should consume facade DTOs.

---

## 14. Versioning & Configuration

| Constant | Value |
|---|---|
| `DASHBOARD_VERSION` | `"1.0.0"` |
| `DASHBOARD_UI_SCHEMA_VERSION` | `"1.0.0"` |

Align bind host/port with `ApplicationConfiguration.dashboard` when launching via process wrapper; Streamlit’s own port may differ — document launch command:

```bash
streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8080
```

Map `THETA_DASHBOARD_*` env vars through Application Configuration — dashboard reads resolved config via facade/settings view, not ad-hoc `os.environ` for secrets.

---

## 15. Future Live Updates

### 15.1 v1 mechanism

- Manual Refresh button
- Optional autorefresh every `refresh_interval_seconds` when `enable_autorefresh=True`

### 15.2 v1.1+ bridge (design only — do not implement domain logic)

```text
EventBus subscriber thread
  → ThreadSafeSnapshotBuffer
  → on Streamlit rerun, facade reads latest buffer snapshot
```

**Rule LIVE-001:** Event subscriber must never call trading APIs.

**Rule LIVE-002:** Buffer stores immutable snapshots only, bounded length.

---

## 16. Error Handling & Observability

| Code | Meaning |
|---|---|
| `DASH.FACADE.UNAVAILABLE` | Backend session missing |
| `DASH.FACADE.START_FAILED` | Start delegation failed |
| `DASH.FACADE.STOP_FAILED` | Stop delegation failed |
| `DASH.PAGE.RENDER_FAILED` | Page exception caught by shell |
| `DASH.CONFIG.INVALID` | UI config validation failed |
| `CFG-DASH-001`… | UI config field invariants |

Log events: `dashboard.shell.start`, `dashboard.page.render`, `dashboard.facade.action`, `dashboard.error`.

Never log tokens, API keys, or access tokens.

---

## 17. Testing Requirements

### 17.1 Coverage targets

- Unit tests for: config validation, formatters, view-model builders, page registry, null facade, forbidden-import guard.
- Smoke tests: each `render()` callable with `NullIntegrationFacade` under Streamlit testing utilities or by invoking render functions with mocked `st` (dependency injection preferred).
- Target: **≥ 90%** coverage on pure Python modules (`config`, `view_models`, `facade`, `utils`, non-Streamlit components). Streamlit widget lines may be lightly smoke-tested.

### 17.2 Mandatory tests

| ID | Test |
|---|---|
| T01 | `DashboardUiConfig` defaults + CFG invariants |
| T02 | Page registry contains exactly 11 pages |
| T03 | Each page `render` runs with null facade without raising |
| T04 | Sidebar page ids match registry |
| T05 | Home placeholders include NIFTY, BANKNIFTY, SENSEX, INDIA VIX |
| T06 | KPI cards render five labels |
| T07 | Chart placeholder renders without TradingView JS dependency |
| T08 | Start/Stop/Refresh call facade mocks (not engines) |
| T09 | Forbidden import guard fails on synthetic broker import |
| T10 | Formatters: money/percent/timestamp deterministic |
| T11 | Settings view redacts secret-looking keys |
| T12 | Autorefresh flag does not trigger trading cycle |

### 17.3 Manual DoD checklist

- [ ] `streamlit run dashboard/app.py` starts
- [ ] Navigation works for all 11 pages
- [ ] All pages render
- [ ] Dark theme visible
- [ ] Professional layout on desktop width
- [ ] No backend logic in dashboard callbacks (code review)

---

## 18. Public API / Exports

`dashboard/__init__.py` exports:

```text
DASHBOARD_VERSION
DASHBOARD_UI_SCHEMA_VERSION
DashboardUiConfig
default_dashboard_ui_config()
```

Runtime entry remains `dashboard/app.py` for Streamlit.

---

## 19. Definition of Done

The dashboard foundation is **done** when all are true:

1. Package structure exists as specified under `dashboard/`.
2. `dashboard/app.py` starts successfully under Streamlit.
3. Sidebar navigation reaches all eleven pages.
4. Every page renders without uncaught exceptions (placeholders acceptable).
5. Home shows index placeholders (NIFTY, BANKNIFTY, SENSEX, INDIA VIX), KPI cards, and TradingView chart placeholder.
6. Dark theme applied; layout professional and responsive.
7. Start / Stop / Refresh delegate to facade only.
8. No trading logic, broker logic, or database logic inside `dashboard/`.
9. Fully typed public functions/classes with Google-style docstrings.
10. Versioned `DashboardUiConfig` schema `1.0.0`.
11. Future-ready hooks for live updates documented and optionally flag-gated.
12. Tests/guards prove presentation-only boundaries.
13. Backend architecture remains unmodified except unavoidable allowlisted read-only facade usage.

---

## Appendix A — Home Wireframe

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ Sidebar │  NIFTY | BANKNIFTY | SENSEX | INDIA VIX                         │
│  Nav    │  ─────   ────────   ──────   ────────                           │
│  Status │                                                                  │
│  Mode   │  [Active Strategy] [Confidence] [Regime] [Paper PnL] [Positions]│
│  Start  │                                                                  │
│  Stop   │  ┌────────────────────────────────────────────────────────────┐ │
│  Refresh│  │         TradingView Lightweight Charts PLACEHOLDER         │ │
│         │  │              (center chart region)                         │ │
│         │  └────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Appendix B — Page Independence Rule

Each file under `pages/` must be removable without breaking other pages (only registry update required). Shared behavior lives in `components/` and `utils/` only.

---

## Appendix C — Out of Scope (v1.1+)

- Full TradingView Lightweight Charts JS integration
- WebSocket push UI
- Order placement / cancellation from UI
- User RBAC beyond token gate
- Mobile-native app
- Embedded Jupyter / research IDE
- Direct SQL dashboards

---

## Appendix D — Implementer Checklist

- [ ] Create package directories and `__init__.py` files
- [ ] Implement `DashboardUiConfig` + theme CSS
- [ ] Implement `NullIntegrationFacade` + optional Integration Session adapter
- [ ] Implement sidebar + components
- [ ] Implement 11 page modules with placeholders
- [ ] Wire `app.py` shell
- [ ] Add forbidden-import unit test
- [ ] Smoke-run Streamlit locally
- [ ] Confirm no unrelated backend modules modified

---

**End of specification — Dashboard Foundation (`dashboard/app.py`) v1.0.0**
