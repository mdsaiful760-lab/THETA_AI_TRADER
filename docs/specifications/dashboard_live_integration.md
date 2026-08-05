# Dashboard Live Integration — Software Engineering Specification

| Field | Value |
|---|---|
| **Scope** | Streamlit dashboard ↔ `DashboardFacade` ↔ existing live modules (read-only) |
| **Primary modules** | `dashboard/live_session_adapter.py`, `dashboard/app.py`, `dashboard/dashboard_facade.py` |
| **Document version** | 1.0.0 |
| **Status** | Normative — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Connect the existing dashboard presentation layer to **already-running** platform modules so the Home / Strategy / Paper views can show **real values** when live sources are injected, and **OFFLINE / `—` placeholders** when they are not.

### Connected existing modules (consume only)

| Module | Path | Dashboard use |
|---|---|---|
| Kite WebSocket | `broker/kite_websocket.py` | Connection status / health (no subscribe, no open) |
| Market data streaming | `broker/market_data_streaming.py` | Index LTPs / snapshots / last update |
| Market snapshot model | `market_data/market_snapshot.py` | Field mapping (`UnderlyingSnapshot`, `VolatilitySnapshot`) |
| Paper trading runner | `paper_trading/paper_trading_runner.py` | Cash / PnL / open positions (read getters only) |
| Strategy scoring framework | `strategy/strategy_scoring_framework.py` | Health/statistics observability only (never `score()`) |
| Strategy evaluation bundle | cached upstream object | Active strategy / confidence / regime context when provided |

### Architecture freeze

- **Do not** create new engines, strategies, or broker components.
- **Do not** place orders or run trading cycles from the dashboard.
- **Do not** call `StrategyScoringFramework.score()` or evaluation `evaluate*()` on refresh.
- **Do not** call `PaperTradingRunner.simulate_plan()` / `mark_to_market()` for display refresh.
- **Do not** open or drive `KiteWebSocketClient` from the dashboard.
- Preserve offline mode when no live handles are registered.

### Pipeline

```text
[Existing live process injects optional handles]
    MarketDataStreamingEngine (pull snapshots)
    KiteWebSocketClient (status/health only)
    PaperTradingRunner (get_portfolio_view / capital / book)
    Cached StrategyEvaluationBundle provider (optional)
    StrategyScoringFramework (health/statistics only)
              ↓
[dashboard/live_session_adapter.py]  DashboardLiveSessionAdapter
    implements IntegrationSessionLike + soft getters
              ↓
[dashboard/dashboard_facade.py]  DashboardFacade(session=adapter)
              ↓
[dashboard pages]  Home / Strategy Monitor / Paper Trading
```

---

## 2. Responsibilities

| # | Responsibility |
|---|---|
| R1 | Provide `DashboardLiveSessionAdapter` implementing facade soft-read accessors. |
| R2 | Map streaming snapshots → index quotes for NIFTY, BANKNIFTY, SENSEX, INDIA VIX. |
| R3 | Map websocket/streaming health → connection status labels. |
| R4 | Map paper runner portfolio → paper PnL / open positions. |
| R5 | Map cached evaluation bundle → active strategy / confidence / strategy rows. |
| R6 | Soft-read scoring framework `health()` / `statistics()` only. |
| R7 | Default offline when handles absent. |
| R8 | Thread-safe adapter reads (`RLock`). |
| R9 | Full deterministic unit tests with stubs (no live network). |
| R10 | Document registration / offline default in this spec. |

---

## 3. Non-Responsibilities

| # | Forbidden |
|---|---|
| NR1 | New Market / Strategy / Broker engines |
| NR2 | Order placement / paper simulation |
| NR3 | Strategy evaluation or scoring on UI refresh |
| NR4 | Opening WebSocket connections from Streamlit |
| NR5 | Redesigning `IntegrationSession` internals |

---

## 4. Adapter API (normative)

### 4.1 Handles

```python
@dataclass
class DashboardLiveHandles:
    integration_session: object | None = None   # get_health / get_runtime_state
    market_streaming: object | None = None      # get_snapshot / get_health
    kite_websocket: object | None = None        # get_status / get_health
    paper_runner: object | None = None          # get_portfolio_view / capital / book
    evaluation_bundle_provider: Callable[[], object | None] | None = None
    market_regime_provider: Callable[[], object | None] | None = None
    scoring_framework: object | None = None     # health() / statistics() only
```

### 4.2 Session methods implemented

| Method | Behavior when source missing |
|---|---|
| `get_health` / `get_runtime_state` | Delegate integration session, else DISCONNECTED / ANALYSIS |
| `get_index_quotes` | Build four-symbol quotes from streaming; empty → facade offline cards |
| `get_home_market_indices` | Optional pre-aggregated payload; else omit (facade uses quotes) |
| `get_market_snapshot` | From streaming NIFTY (or first available) snapshot |
| `get_strategy_status` / `get_strategy_evaluation_summary` | From cached bundle provider |
| `get_market_regime` | From regime provider or bundle metadata |
| `get_paper_trading_snapshot` / `get_paper_positions` | From paper runner read getters |
| `get_order_book` | Empty book unless an orders provider is later attached (optional) |

### 4.3 Display fields covered (Home)

- NIFTY / BANKNIFTY / SENSEX / INDIA VIX LTP + change + last update + connection status
- Market Regime, Active Strategy, Strategy Confidence
- Paper PnL, Open Positions

---

## 5. Mapping rules (display-only)

### Indices

| Symbol | Source |
|---|---|
| NIFTY / BANKNIFTY / SENSEX | `market_streaming.get_snapshot(symbol).underlying` |
| INDIA VIX | `snapshot.volatility` from any available index snapshot (prefer NIFTY) |

Fields: `last_price`→ltp, `change`→change_abs, `change_percent`→change_pct, `quote_timestamp`→timestamp.

Connection status:

| Condition | Label |
|---|---|
| WS connected + fresh quote | `LIVE` |
| Connected but stale / missing LTP | `DELAYED` / `UNKNOWN` |
| WS disconnected / no streaming | `OFFLINE` |

### Paper

| Display | Source |
|---|---|
| Paper PnL | `portfolio.total_unrealized_pnl` (or realized+unrealized summary) |
| Open Positions | `len(portfolio.positions.positions)` / `open_position_count` |
| Ledger fields | Existing facade paper mapping |

### Strategy

| Display | Source |
|---|---|
| Active Strategy | `summary.top_strategy_id` / selected id |
| Confidence | top report `confidence.overall_score` |
| Regime | `market_regime_provider` or bundle metadata `market_regime` |

**Never** call scoring `score()` or evaluation `evaluate()` from the adapter.

---

## 6. Offline preservation

| Condition | Result |
|---|---|
| No handles registered | `DashboardFacade(session=None)` — full offline placeholders |
| Partial handles | Map available domains; others placeholder / empty |
| Upstream exception | Soft-degrade; never crash Streamlit |

Registration:

```python
register_live_handles(handles)   # process bootstrap
clear_live_handles()             # tests / shutdown
build_default_presentation_facade()
```

`app.py` uses `build_default_presentation_facade()` so offline remains default until handles are registered.

---

## 7. Thread safety & typing

- Adapter methods take `RLock`.
- No shared mutable quote dicts owned by UI.
- Full type hints; Google docstrings on public API.

---

## 8. Testing

| ID | Test |
|---|---|
| T01 | No handles → offline facade / OFFLINE indices |
| T02 | Streaming stub maps four symbols incl. INDIA VIX |
| T03 | Websocket disconnected → OFFLINE connection status |
| T04 | Paper runner stub maps PnL + open positions into home KPIs |
| T05 | Evaluation bundle stub maps active strategy + confidence |
| T06 | Scoring framework `score()` never called |
| T07 | Paper `simulate_plan` never called |
| T08 | Adapter is thread-safe under concurrent reads |
| T09 | `register_live_handles` / `clear_live_handles` round-trip |

---

## 9. Files to touch

| File | Change |
|---|---|
| `docs/specifications/dashboard_live_integration.md` | This specification |
| `dashboard/live_session_adapter.py` | Adapter + handles + factory |
| `dashboard/app.py` | Use default presentation facade factory |
| `tests/test_dashboard_live_integration.py` | Deterministic stub tests |
| `CHANGELOG.md` | Feature note |

**Forbidden:** edits to `broker/*`, `paper_trading/*`, `strategy/*` internals for this feature.

---

## 10. Definition of Done

1. Live handles produce real index / strategy / paper values through `DashboardFacade`.
2. Absent engines → graceful OFFLINE / `—` without crash.
3. Offline default preserved.
4. Read-only; no execution / order placement / strategy computation.
5. Thread-safe; typed; documented; pytest coverage with deterministic stubs.

---

**End of specification — Dashboard Live Integration v1.0.0**
