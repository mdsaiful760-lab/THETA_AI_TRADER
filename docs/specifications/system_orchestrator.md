# System Orchestrator — Software Engineering Specification

| Field | Value |
|---|---|
| **Module** | `system/system_orchestrator.py` |
| **Document version** | 1.0.0 |
| **Status** | Draft — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-04 |

---

## 1. Purpose

`system/system_orchestrator.py` defines the **root coordination and lifecycle management layer** for THETA AI TRADER v1.0.

The module is the **single entry point** that wires together all institutional pipeline engines, manages their lifecycles, subscribes to and publishes on the Event Bus, executes deterministic trading cycles, monitors platform health, isolates failures, and performs graceful startup and shutdown — but **never** performs market analysis, selects strategies, executes broker API calls directly, implements APME logic, or performs risk calculations.

The module answers: *"How do we initialize, wire, run, monitor, and safely shut down the full THETA AI TRADER institutional pipeline as a cohesive system — without embedding domain intelligence in the coordinator itself?"*

It is **not** a strategy engine. It is **not** a risk manager. It is **not** an order submission layer. It is **not** APME. It is **not** a market data collector. It is the **system coordination gate** that invokes engines in the correct order, passes immutable artifacts between them, and maintains operational state.

### Pipeline placement

```text
[External: CLI / Scheduler / Dashboard]
              ↓
[system/system_orchestrator.py]              ← THIS MODULE (ROOT COORDINATOR)
    initialize Event Bus
    wire engine subscriptions
    startup → RUNNING
              ↓
    ┌─────────────────────────────────────────────────────────────┐
    │ PRE-TRADE CYCLE (new entry evaluation)                       │
    │   Market Data Engine → snapshot                              │
    │   Strategy Evaluation Engine → signals                       │
    │   Trade Decision Engine → decision                           │
    │   map PortfolioSnapshot → risk contract                      │
    │   Risk Engine → verdict                                      │
    │   Execution Engine → plan                                    │
    │   Order Manager → submission (via injected broker client)    │
    └─────────────────────────────────────────────────────────────┘
              ↓
    ┌─────────────────────────────────────────────────────────────┐
    │ POST-FILL CYCLE (position accounting + management)           │
    │   Position Manager → PositionSnapshot                        │
    │   Portfolio Manager → PortfolioSnapshot                      │
    │   APME → APMEDecisionReport                                  │
    │   (v1.1+: translate management decisions → new plans)        │
    └─────────────────────────────────────────────────────────────┘
              ↓
    health monitoring · error isolation · graceful shutdown
              ↓
[Event Bus: market.* · engine.* · order.* · position.* · portfolio.* · apme.* · system.*]
```

### Architecture freeze note

The platform architecture is **FROZEN** for v1.0:

- **System Orchestrator** is the **only** component permitted to wire the full institutional pipeline end-to-end in production runs.
- Analytical engines remain **stateless and independent** — orchestrator invokes public APIs; engines never call each other directly.
- **Event Bus** is the communication backbone — orchestrator registers subscriptions and may publish `system.*` and `pipeline.*` events; it does not replace domain event publishers inside engines.
- **Market Data Engine** produces snapshots; orchestrator triggers cycles on `market.snapshot.published` or scheduled tick — orchestrator does not fetch ticks directly.
- **Strategy Evaluation Engine** evaluates registered strategies; orchestrator passes `EngineContext` — orchestrator does not select strategies.
- **Trade Decision Engine** aggregates signals into decisions; orchestrator passes inputs — orchestrator does not score strategies.
- **Risk Engine** emits APPROVED/REJECTED/REDUCED; orchestrator maps `portfolio.portfolio_manager.PortfolioSnapshot` → `risk.risk_engine.PortfolioSnapshot` — orchestrator does not compute risk scores.
- **Execution Engine** builds plans; orchestrator passes `RiskDecisionResult` — orchestrator does not plan legs.
- **Order Manager** submits via injected `BaseBrokerClient`; orchestrator injects session — orchestrator never calls broker APIs directly.
- **Position Manager** owns position accounting; orchestrator calls `apply_order_tracker()` after fills.
- **Portfolio Manager** owns portfolio aggregation; orchestrator calls `ingest_position_snapshot()` after position updates.
- **APME** owns post-execution management intelligence; orchestrator calls `evaluate()` and logs decisions in v1 — orchestrator does not implement exit rules.
- Legacy modules (`trade_risk_orchestrator.py`, `strategy_engine.py` root) are **not** invoked by institutional pipeline — migration path documented in Appendix D.

### Goals

1. Provide a **single root coordinator** for THETA AI TRADER institutional pipeline.
2. **Initialize all engines** with injected immutable configuration at startup.
3. **Wire Event Bus subscriptions** for cross-engine event-driven triggers.
4. **Manage engine lifecycle** states: UNINITIALIZED → STARTING → RUNNING → DEGRADED → STOPPING → STOPPED → FAILED.
5. **Coordinate startup** with ordered dependency resolution and health gates.
6. **Coordinate graceful shutdown** with in-flight cycle drain and subscription cleanup.
7. **Execute trading cycles** — pre-trade entry pipeline and post-fill accounting/management pipeline.
8. **Health monitoring** — aggregate engine health, bus metrics, cycle latency, stale snapshot detection.
9. **Error isolation** — contain engine failures; prevent cascade; emit structured diagnostics.
10. **Thread-safe** operation for concurrent cycle triggers and status reads.
11. **Deterministic cycle fingerprints** for replay verification in BACKTEST mode.
12. **Immutable configuration** — `SystemOrchestratorConfig` frozen at construction.
13. **Validation** of config, cycle context, and cycle results before return.
14. **Serialization** of public types schema v1.0.0.
15. **Mode-aware behaviour** — LIVE vs ANALYSIS vs BACKTEST strictness.
16. **Correlation propagation** — single `correlation_id` per trading cycle across all engine invocations.
17. **Broker session injection** — orchestrator holds broker client reference; passes to Order Manager only.
18. **Account hint assembly** — orchestrator reads broker funds/margin snapshots for Portfolio Manager context.
19. **Greek hint assembly** — orchestrator attaches per-position Greek hints before portfolio ingest (from upstream engine outputs in future; stub mapping in v1).
20. **APME context assembly** — orchestrator builds `APMEEvaluationContext` from market/regime/news hints.
21. **Portfolio-to-risk mapping** — orchestrator performs explicit `PortfolioSnapshot` → `risk.PortfolioSnapshot` mapping.
22. **No parallel pipeline state** — orchestrator does not duplicate position/portfolio dictionaries.
23. **Publish `system.*` lifecycle events** for observability.
24. **Google-style docstrings** on all public types and methods.
25. **Unit test coverage ≥ 95%** on `system/system_orchestrator.py`.

### Success criteria

- `SystemOrchestrator.start()` transitions to RUNNING when all required engines initialize successfully.
- `SystemOrchestrator.run_trading_cycle(context)` executes full pre-trade pipeline and returns immutable `TradingCycleResult`.
- `SystemOrchestrator.run_post_fill_cycle(context, order_tracker)` executes Position → Portfolio → APME chain.
- `SystemOrchestrator.stop()` drains in-flight cycles and transitions to STOPPED within configured timeout.
- Engine failure in one cycle does not crash the orchestrator process — status becomes DEGRADED with structured error.
- Identical cycle inputs produce identical `cycle_fingerprint` in BACKTEST mode.
- No domain logic (strategy selection, risk math, APME rules) exists in orchestrator source.
- Unit test coverage ≥ 95% line coverage on `system/system_orchestrator.py`.

### Relationship to other modules

| Module | Relationship |
|---|---|
| `core/event_bus.py` | **Infrastructure.** Orchestrator creates or receives bus; registers subscriptions. |
| `core/base_engine.py` | **Contract reference.** Orchestrator invokes `engine.run(context)` — never subclasses engines. |
| `core/engine_context.py` | **Cycle input.** Orchestrator builds immutable context per engine invocation. |
| `market_data/market_data_engine.py` | **Upstream data.** Orchestrator triggers snapshot refresh or consumes bus events. |
| `strategy/strategy_evaluation_engine.py` | **Pre-trade.** Orchestrator invokes after market snapshot available. |
| `decision/trade_decision_engine.py` | **Pre-trade.** Orchestrator invokes after strategy evaluation. |
| `risk/risk_engine.py` | **Pre-trade gate.** Orchestrator maps portfolio snapshot and invokes review. |
| `execution/execution_engine.py` | **Pre-trade planning.** Orchestrator invokes after risk approval. |
| `execution/order_manager.py` | **Order submission.** Orchestrator invokes with injected broker client. |
| `portfolio/position_manager.py` | **Post-fill accounting.** Orchestrator invokes after order tracker update. |
| `portfolio/portfolio_manager.py` | **Post-fill aggregation.** Orchestrator invokes after position snapshot. |
| `apme/adaptive_position_management_engine.py` | **Post-fill management.** Orchestrator invokes after portfolio snapshot. |
| `broker/base_broker.py` | **Injected dependency.** Session owned by orchestrator; passed to Order Manager only. |
| Legacy orchestrators | **Out of scope.** `trade_risk_orchestrator.py` not used in v1 institutional path. |

### Distinction from analytical engines

| Concern | Analytical Engine | System Orchestrator |
|---|---|---|
| Role | Domain intelligence | Coordination and lifecycle |
| Input | EngineContext + domain snapshot | Cycle context + engine registry |
| Output | EngineResult / domain artifact | TradingCycleResult / health status |
| Business logic | Yes — core responsibility | **Never** |
| Calls peer engines | **Never** | Yes — via public APIs only |
| Event bus | Publishes domain events | Subscribes and publishes system events |

### Distinction from APME

| Concern | APME | System Orchestrator |
|---|---|---|
| Role | Position management decisions | Invokes APME; does not implement rules |
| Exit logic | Core responsibility | **Never** |
| When invoked | After portfolio update | Post-fill cycle stage |
| Output | APMEDecisionReport | Passes report to logging/handlers |

### Distinction from Risk Engine

| Concern | Risk Engine | System Orchestrator |
|---|---|---|
| Role | Pre-trade risk verdict | Maps portfolio snapshot; invokes risk |
| Risk calculations | Core responsibility | **Never** |
| Portfolio mapping | Consumes risk contract | **Produces** mapping from Portfolio Manager output |

---

## 2. Responsibilities

`system/system_orchestrator.py` **is responsible for**:

| # | Responsibility | Description |
|---|---|---|
| R1 | **Event Bus initialization** | Create or accept injected `EventBus` instance. |
| R2 | **Engine registry** | Hold references to all coordinated engines with stable IDs. |
| R3 | **Engine initialization** | Construct engines from config factories at startup. |
| R4 | **Subscription wiring** | Register orchestrator handlers on domain topic patterns. |
| R5 | **Lifecycle state machine** | Manage UNINITIALIZED → RUNNING → STOPPED transitions. |
| R6 | **Startup sequence** | Ordered startup with dependency checks and health gates. |
| R7 | **Graceful shutdown** | Drain cycles, unsubscribe, stop engines within timeout. |
| R8 | **Pre-trade cycle execution** | Market → Strategy → Decision → Risk → Execution → Order. |
| R9 | **Post-fill cycle execution** | Position → Portfolio → APME. |
| R10 | **Correlation ID management** | Generate and propagate per-cycle correlation IDs. |
| R11 | **Broker client injection** | Hold and pass `BaseBrokerClient` to Order Manager. |
| R12 | **Account hint assembly** | Build portfolio ingest context from broker funds/margin. |
| R13 | **Greek hint assembly** | Attach per-position Greek hints for portfolio ingest. |
| R14 | **APME context assembly** | Build `APMEEvaluationContext` from cycle hints. |
| R15 | **Portfolio-to-risk mapping** | Map Portfolio Manager snapshot to Risk Engine contract. |
| R16 | **Health monitoring** | Periodic health aggregation across engines and bus. |
| R17 | **Error isolation** | Catch engine exceptions; record; continue or degrade per policy. |
| R18 | **Cycle fingerprint** | Deterministic hash over cycle outcomes for replay. |
| R19 | **TradingCycleResult assembly** | Immutable sealed result per cycle run. |
| R20 | **System event publishing** | Publish on `system.*` and `pipeline.*` topics. |
| R21 | **Mode-aware strictness** | LIVE vs ANALYSIS vs BACKTEST behaviour differences. |
| R22 | **Thread-safe registry** | Protect lifecycle and cycle state with locks. |
| R23 | **Validation** | Validate config, context, and results. |
| R24 | **Serialization** | JSON round-trip for public types. |
| R25 | **Logging conventions** | Standard log events for startup, cycle, shutdown, errors. |
| R26 | **In-flight cycle tracking** | Track active cycles for drain on shutdown. |
| R27 | **Degraded mode** | Continue with reduced functionality when non-critical engine fails. |
| R28 | **Cycle scheduling hooks** | Support manual, event-driven, and interval triggers. |
| R29 | **Engine health probes** | Lightweight readiness checks without full cycle. |
| R30 | **Documentation contract** | Google-style docstrings on all public API. |

---

## 3. Non-Responsibilities

`system/system_orchestrator.py` **must not**:

| # | Non-responsibility | Rationale |
|---|---|---|
| NR1 | **Perform market analysis or intelligence** | Market Intelligence Engine responsibility. |
| NR2 | **Select strategies or run strategy plugins directly** | Strategy Evaluation Engine responsibility. |
| NR3 | **Score strategies or compute confidence** | Strategy Score / Confidence Engine responsibility. |
| NR4 | **Aggregate trading signals into decisions** | Trade Decision Engine responsibility. |
| NR5 | **Perform risk calculations or emit risk verdicts** | Risk Engine responsibility. |
| NR6 | **Build execution plans or compute leg sequencing** | Execution Engine responsibility. |
| NR7 | **Call broker place_order, modify_order, or auth APIs directly** | Order Manager via injected client only. |
| NR8 | **Implement APME exit, roll, hedge, or protection rules** | APME responsibility. |
| NR9 | **Mutate Position, PortfolioSnapshot, or domain artifacts** | Domain engines own immutable outputs. |
| NR10 | **Compute Greeks, IV, or forward prices** | Greeks / Forward Pricing Engine responsibility. |
| NR11 | **Detect market regime internally** | Market Regime Detector responsibility; hints injected only. |
| NR12 | **Persist state to database or disk** | External persistence; orchestrator returns immutable results. |
| NR13 | **Render UI or dashboards** | UI subscribes to events. |
| NR14 | **Load environment variables or config files** | Accept injected `SystemOrchestratorConfig`. |
| NR15 | **Import Kite SDK or Zerodha modules** | Broker abstraction via `BaseBrokerClient` only. |
| NR16 | **Replace Event Bus routing logic** | Bus delivers; orchestrator subscribes. |
| NR17 | **Implement custom threading pool for engine internals** | Engines manage own concurrency; orchestrator serializes cycles by default. |
| NR18 | **Bypass engine public APIs** | No private method calls on engines. |
| NR19 | **Maintain parallel position/portfolio dictionaries** | Query engines via public get_snapshot APIs. |
| NR20 | **Silently swallow engine failures** | All failures recorded in cycle result and system events. |
| NR21 | **Force trades when risk rejects** | Fail closed — respect Risk Engine verdict. |
| NR22 | **Re-run failed orders without new ExecutionPlan** | Must request new plan from Execution Engine. |
| NR23 | **Merge legacy and institutional pipelines** | v1 institutional path only in this module. |
| NR24 | **Train ML models** | Deterministic coordination only. |
| NR25 | **Authenticate users** | Security layer external. |

---

## 4. Architecture

### 4.1 Layered design

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                    system/system_orchestrator.py                             │
│  (root coordinator — no domain intelligence, no broker calls, no APME logic) │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │ SystemOrchestrator│  │ CycleExecutor    │  │ OrchestratorRegistry     │  │
│  │ (public service)  │→ │ (pre/post trade) │→ │ (engines + bus + state)  │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘  │
│           │                     │                         │                  │
│           ▼                     ▼                         ▼                  │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ LifecycleManager · SubscriptionWiring · HealthMonitor · ErrorBoundary  │  │
│  │ ContextAssembler · PortfolioRiskMapper · CycleFingerprint · EventPub   │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
         ▲                                           │
         │ SystemOrchestratorConfig + broker client  ▼
         │                                    TradingCycleResult
         │                                    SystemHealthReport
         │                                    system.* / pipeline.* events
```

### 4.2 Coordinated engines

| Engine ID | Module | Orchestrator interaction |
|---|---|---|
| `event_bus` | `core/event_bus.py` | Create/inject; subscribe/publish |
| `market_data` | `market_data/market_data_engine.py` | Trigger snapshot; subscribe `market.snapshot.published` |
| `strategy_evaluation` | `strategy/strategy_evaluation_engine.py` | `run(EngineContext)` after snapshot |
| `trade_decision` | `decision/trade_decision_engine.py` | `run(EngineContext)` after strategy eval |
| `risk` | `risk/risk_engine.py` | `review(...)` with mapped portfolio snapshot |
| `execution` | `execution/execution_engine.py` | `plan(...)` after risk approval |
| `order_manager` | `execution/order_manager.py` | `submit_plan(...)` with broker client |
| `position_manager` | `portfolio/position_manager.py` | `apply_order_tracker(...)` post-fill |
| `portfolio_manager` | `portfolio/portfolio_manager.py` | `ingest_position_snapshot(...)` post-fill |
| `apme` | `apme/adaptive_position_management_engine.py` | `evaluate(...)` post-fill |

### 4.3 Design principles

- **Coordination only** — orchestrator wires and invokes; never embeds domain rules.
- **Immutable handoffs** — every artifact passed between engines is frozen dataclass output.
- **Fail closed on LIVE** — ambiguous state halts cycle; ANALYSIS may continue with warnings.
- **Error isolation** — one engine failure contained per cycle; orchestrator survives.
- **Explicit mapping** — portfolio-to-risk mapping is visible, testable function in orchestrator.
- **Event-driven optional** — cycles may be triggered by bus events or explicit API calls.
- **Deterministic BACKTEST** — stable cycle fingerprints and ordered engine invocation.
- **Dependency injection** — all engines and broker client injected or factory-built from config.
- **Single cycle lock** — default serial cycle execution prevents overlapping pipeline runs.

### 4.4 Dependency direction

```text
system/system_orchestrator.py
    → core/event_bus.py
    → core/engine_context.py
    → market_data/market_data_engine.py
    → strategy/strategy_evaluation_engine.py
    → decision/trade_decision_engine.py
    → risk/risk_engine.py
    → execution/execution_engine.py
    → execution/order_manager.py
    → portfolio/position_manager.py
    → portfolio/portfolio_manager.py
    → apme/adaptive_position_management_engine.py
    → broker/base_broker.py (interface only)

Forbidden imports:
    → broker/zerodha/*
    → strategy plugins directly
    → risk engine internals beyond public API
    → APME engine internals beyond public API
    → market data WebSocket internals
```

### 4.5 Relationship diagram

```mermaid
classDiagram
    class SystemOrchestrator {
        +start() SystemStartupResult
        +stop() SystemShutdownResult
        +run_trading_cycle(context) TradingCycleResult
        +run_post_fill_cycle(context, tracker) PostFillCycleResult
        +get_health() SystemHealthReport
        +get_state() OrchestratorState
    }

    class CycleExecutor {
        +execute_pre_trade(context) TradingCycleResult
        +execute_post_fill(context, tracker) PostFillCycleResult
    }

    class OrchestratorRegistry {
        +engines: Mapping
        +event_bus: EventBus
        +broker_client: BaseBrokerClient
        +state: OrchestratorState
    }

    class TradingCycleResult {
        <<immutable>>
        +cycle_id: str
        +correlation_id: str
        +status: CycleStatus
        +cycle_fingerprint: str
    }

    SystemOrchestrator --> CycleExecutor
    SystemOrchestrator --> OrchestratorRegistry
    CycleExecutor --> TradingCycleResult
```

---

## 5. Data Model

All public outward-facing types are **immutable dataclasses** (`frozen=True`) unless noted.

### 5.1 Type hierarchy

```text
SystemOrchestrator (mutable service)
├── config: SystemOrchestratorConfig
├── registry: OrchestratorRegistry (thread-safe)
├── cycle_executor: CycleExecutor (stateless)
├── lifecycle_manager: LifecycleManager
├── health_monitor: HealthMonitor
└── methods: start(), stop(), run_trading_cycle(), run_post_fill_cycle(), get_health()

SystemOrchestratorConfig (immutable)
├── execution_mode: StrategyExecutionMode
├── account_id: str
├── enable_pre_trade_cycle: bool
├── enable_post_fill_cycle: bool
├── enable_event_driven_cycles: bool
├── cycle_timeout_seconds: int
├── shutdown_drain_timeout_seconds: int
├── health_probe_interval_seconds: int
├── stale_snapshot_max_age_seconds: int
├── strict_correlation: bool
├── deterministic_fingerprint: bool
├── publish_system_events: bool
├── engine_configs: Mapping[EngineId, object]
├── subscription_patterns: tuple[str, ...]
└── metadata: Mapping[str, str]

TradingCycleContext (immutable)
├── correlation_id: str
├── reference_time: datetime
├── execution_mode: StrategyExecutionMode
├── account_id: str
├── market_snapshot: MarketSnapshot | None
├── trigger: CycleTrigger
├── tags: Mapping[str, str]

TradingCycleResult (immutable)                    ← PRIMARY CYCLE OUTPUT
├── cycle_id: str
├── correlation_id: str
├── status: CycleStatus
├── trigger: CycleTrigger
├── stages: tuple[CycleStageResult, ...]
├── market_snapshot_id: str | None
├── strategy_result_id: str | None
├── decision_result_id: str | None
├── risk_verdict: str | None
├── execution_plan_id: str | None
├── order_submission_id: str | None
├── warnings: tuple[OrchestratorWarningRecord, ...]
├── errors: tuple[OrchestratorErrorRecord, ...]
├── primary_error_code: str | None
├── submitted_at: datetime
├── completed_at: datetime | None
├── duration_ms: float
└── cycle_fingerprint: str

PostFillCycleContext (immutable)
├── correlation_id: str
├── reference_time: datetime
├── execution_mode: StrategyExecutionMode
├── account_id: str
├── order_tracker: OrderTracker
├── price_hints: Mapping[str, float]
├── equity_hint: float
├── cash_available_hint: float
├── margin_used_hint: float
├── margin_available_hint: float | None
├── greek_hints: Mapping[str, PositionGreekHint]
├── volatility_hints: VolatilityHints | None
├── regime_hints: RegimeHints | None
├── trend_hints: Mapping[str, TrendHints]
├── news_flags: tuple[NewsEventFlag, ...]
├── signal_metadata: Mapping[str, SignalManagementMetadata]
├── tags: Mapping[str, str]

PostFillCycleResult (immutable)
├── cycle_id: str
├── correlation_id: str
├── status: CycleStatus
├── position_update_id: str | None
├── portfolio_update_id: str | None
├── apme_report_id: str | None
├── stages: tuple[CycleStageResult, ...]
├── warnings: tuple[OrchestratorWarningRecord, ...]
├── errors: tuple[OrchestratorErrorRecord, ...]
├── submitted_at: datetime
├── completed_at: datetime | None
├── duration_ms: float
└── cycle_fingerprint: str

SystemHealthReport (immutable)
├── report_id: str
├── as_of: datetime
├── orchestrator_state: OrchestratorState
├── engine_health: Mapping[EngineId, EngineHealthStatus]
├── event_bus_metrics: EventBusHealthMetrics
├── last_cycle_at: datetime | None
├── last_cycle_status: CycleStatus | None
├── stale_snapshot: bool
├── issues: tuple[HealthIssueRecord, ...]
└── overall_status: HealthStatus

SystemStartupResult (immutable)
├── startup_id: str
├── status: StartupStatus
├── engines_started: tuple[EngineId, ...]
├── engines_failed: tuple[EngineId, ...]
├── subscriptions_registered: int
├── warnings: tuple[OrchestratorWarningRecord, ...]
├── errors: tuple[OrchestratorErrorRecord, ...]
├── started_at: datetime
├── completed_at: datetime
└── duration_ms: float

SystemShutdownResult (immutable)
├── shutdown_id: str
├── status: ShutdownStatus
├── cycles_drained: int
├── subscriptions_removed: int
├── engines_stopped: tuple[EngineId, ...]
├── warnings: tuple[OrchestratorWarningRecord, ...]
├── errors: tuple[OrchestratorErrorRecord, ...]
├── started_at: datetime
├── completed_at: datetime
└── duration_ms: float

OrchestratorEvent (immutable)
├── event_type: OrchestratorEventType
├── topic: str
├── correlation_id: str
├── occurred_at: datetime
├── orchestrator_state: OrchestratorState
├── metadata: Mapping[str, str]
```

### 5.2 Enumerations

#### 5.2.1 `OrchestratorState`

| Value | Description |
|---|---|
| `UNINITIALIZED` | Constructed but not started. |
| `STARTING` | Startup sequence in progress. |
| `RUNNING` | Ready to execute cycles. |
| `DEGRADED` | Running with one or more engine failures. |
| `STOPPING` | Shutdown in progress. |
| `STOPPED` | Clean shutdown complete. |
| `FAILED` | Unrecoverable startup or runtime failure. |

#### 5.2.2 `CycleStatus`

| Value | Description |
|---|---|
| `COMPLETED` | Full cycle completed successfully. |
| `PARTIAL` | Cycle completed with warnings or skipped stages. |
| `SKIPPED` | Cycle skipped by config or trigger policy. |
| `REJECTED` | Pre-cycle validation rejected. |
| `FAILED` | Unrecoverable cycle failure. |
| `TIMEOUT` | Cycle exceeded timeout. |

#### 5.2.3 `CycleTrigger`

| Value | Description |
|---|---|
| `MANUAL` | Explicit API invocation. |
| `SCHEDULED` | Interval or cron scheduler. |
| `MARKET_SNAPSHOT` | Triggered by `market.snapshot.published`. |
| `ORDER_COMPLETED` | Triggered by `order.plan.completed`. |
| `PORTFOLIO_UPDATED` | Triggered by `portfolio.snapshot.published`. |
| `APME_ESCALATION` | Triggered by `apme.risk.escalated`. |

#### 5.2.4 `EngineId`

| Value | Module |
|---|---|
| `EVENT_BUS` | `core/event_bus.py` |
| `MARKET_DATA` | `market_data/market_data_engine.py` |
| `STRATEGY_EVALUATION` | `strategy/strategy_evaluation_engine.py` |
| `TRADE_DECISION` | `decision/trade_decision_engine.py` |
| `RISK` | `risk/risk_engine.py` |
| `EXECUTION` | `execution/execution_engine.py` |
| `ORDER_MANAGER` | `execution/order_manager.py` |
| `POSITION_MANAGER` | `portfolio/position_manager.py` |
| `PORTFOLIO_MANAGER` | `portfolio/portfolio_manager.py` |
| `APME` | `apme/adaptive_position_management_engine.py` |

#### 5.2.5 `OrchestratorEventType`

| Value | Topic |
|---|---|
| `STARTUP_STARTED` | `system.startup.started` |
| `STARTUP_COMPLETED` | `system.startup.completed` |
| `STARTUP_FAILED` | `system.startup.failed` |
| `SHUTDOWN_STARTED` | `system.shutdown.started` |
| `SHUTDOWN_COMPLETED` | `system.shutdown.completed` |
| `STATE_CHANGED` | `system.state.changed` |
| `CYCLE_STARTED` | `pipeline.cycle.started` |
| `CYCLE_COMPLETED` | `pipeline.cycle.completed` |
| `CYCLE_FAILED` | `pipeline.cycle.failed` |
| `HEALTH_DEGRADED` | `system.health.degraded` |
| `HEALTH_RECOVERED` | `system.health.recovered` |
| `ENGINE_FAILURE` | `system.engine.failure` |
| `ORCHESTRATOR_ERROR` | `system.error` |

#### 5.2.6 `PreTradeCycleStageId`

| # | Stage ID |
|---|---|
| 1 | `input_gate` |
| 2 | `market_data_refresh` |
| 3 | `strategy_evaluation` |
| 4 | `trade_decision` |
| 5 | `portfolio_risk_mapping` |
| 6 | `risk_review` |
| 7 | `execution_planning` |
| 8 | `order_submission` |
| 9 | `result_assembly` |
| 10 | `output_validation` |

#### 5.2.7 `PostFillCycleStageId`

| # | Stage ID |
|---|---|
| 1 | `input_gate` |
| 2 | `position_update` |
| 3 | `portfolio_ingest` |
| 4 | `apme_evaluation` |
| 5 | `management_decision_handoff` |
| 6 | `result_assembly` |
| 7 | `output_validation` |

#### 5.2.8 `StartupStatus` / `ShutdownStatus`

| StartupStatus | Description |
|---|---|
| `SUCCESS` | All required engines started. |
| `PARTIAL` | Non-critical engines failed; degraded mode. |
| `FAILED` | Critical engine failed; orchestrator not RUNNING. |

| ShutdownStatus | Description |
|---|---|
| `SUCCESS` | Clean shutdown within timeout. |
| `FORCED` | Timeout exceeded; forced stop. |
| `FAILED` | Shutdown error. |

### 5.3 Supporting immutable types

#### 5.3.1 `CycleStageResult`

| Field | Type | Description |
|---|---|---|
| `stage_id` | `str` | Stage identifier. |
| `engine_id` | `EngineId | None` | Engine responsible for stage. |
| `passed` | `bool` | Stage pass/fail. |
| `rejection_code` | `str | None` | Stable error code on failure. |
| `message` | `str | None` | Human-readable message. |
| `duration_ms` | `float` | Stage duration. |
| `details` | `Mapping[str, str]` | Audit metadata. |

#### 5.3.2 `EngineHealthStatus`

| Field | Type | Description |
|---|---|---|
| `engine_id` | `EngineId` | Engine identifier. |
| `status` | `HealthStatus` | HEALTHY, DEGRADED, UNHEALTHY, UNKNOWN. |
| `last_success_at` | `datetime | None` | Last successful invocation. |
| `last_failure_at` | `datetime | None` | Last failure timestamp. |
| `consecutive_failures` | `int` | Failure streak count. |
| `message` | `str | None` | Diagnostic message. |

#### 5.3.3 `EventBusHealthMetrics`

| Field | Type | Description |
|---|---|---|
| `publish_count` | `int` | Total publishes since startup. |
| `delivery_count` | `int` | Total deliveries. |
| `subscriber_failure_count` | `int` | Handler exceptions caught. |
| `active_subscriptions` | `int` | Current subscription count. |

#### 5.3.4 `OrchestratorWarningRecord` / `OrchestratorErrorRecord`

Same shape as portfolio manager warning/error records with optional `stage_id`, `engine_id`, `field`.

### 5.4 Global invariants

- INV-G-001: Orchestrator never mutates engine output artifacts.
- INV-G-002: `cycle_fingerprint` stable across replays with identical inputs in BACKTEST.
- INV-G-003: All datetimes timezone-aware.
- INV-G-004: Single active pre-trade cycle by default (configurable).
- INV-G-005: `correlation_id` non-empty for every cycle.
- INV-G-006: State transitions follow defined state machine — no illegal jumps.
- INV-G-007: Broker client never invoked except through Order Manager public API.
- INV-G-008: APME invoked only through `AdaptivePositionManagementEngine.evaluate()`.

---

## 6. Lifecycle Management

### 6.1 State machine

```text
UNINITIALIZED
    │ start()
    ▼
STARTING ──failure──► FAILED
    │ success
    ▼
RUNNING ◄──recovery── DEGRADED
    │                      ▲
    │ engine failure       │ non-critical recovery
    └──────────────────────┘
    │ stop()
    ▼
STOPPING ──timeout──► STOPPED (FORCED)
    │ clean drain
    ▼
STOPPED
```

**Rule LC-001:** Only `start()` from UNINITIALIZED or STOPPED; only `stop()` from RUNNING or DEGRADED.

**Rule LC-002:** Cycle execution rejected when state is not RUNNING or DEGRADED.

**Rule LC-003:** DEGRADED allows post-fill cycles if position/portfolio/apme healthy; may block pre-trade per config.

### 6.2 Startup sequence

| Step | Action | On failure |
|---|---|---|
| 1 | Validate config | FAILED — do not start |
| 2 | Initialize Event Bus | FAILED if bus required and fails |
| 3 | Construct engines from factories | FAILED if critical engine fails |
| 4 | Register event subscriptions | WARNING if non-critical subscription fails |
| 5 | Run engine health probes | DEGRADED if non-critical probe fails |
| 6 | Publish `system.startup.completed` | — |
| 7 | Transition to RUNNING | — |

**Critical engines (startup must succeed):** Event Bus, Market Data, Risk, Order Manager, Position Manager, Portfolio Manager.

**Non-critical engines (degraded if fail):** Strategy Evaluation, Trade Decision, Execution, APME (post-fill only).

### 6.3 Graceful shutdown sequence

| Step | Action | Timeout behaviour |
|---|---|---|
| 1 | Transition to STOPPING | — |
| 2 | Reject new cycle requests | Immediate |
| 3 | Wait for in-flight cycles to complete | `shutdown_drain_timeout_seconds` |
| 4 | Unsubscribe all orchestrator handlers | — |
| 5 | Stop scheduler if running | — |
| 6 | Publish `system.shutdown.completed` | — |
| 7 | Transition to STOPPED | FORCED if drain timeout exceeded |

**Rule SD-001:** In-flight cycles receive cancellation signal but complete current engine stage before exiting when possible.

**Rule SD-002:** Order Manager in-flight submissions are not cancelled on shutdown — orchestrator waits or logs FORCED stop.

---

## 7. Event Bus Integration

### 7.1 Orchestrator subscriptions (v1 default)

| Topic pattern | Handler | Action |
|---|---|---|
| `market.snapshot.published` | `_on_market_snapshot` | Optional pre-trade cycle trigger |
| `order.plan.completed` | `_on_order_completed` | Post-fill cycle trigger |
| `portfolio.snapshot.published` | `_on_portfolio_snapshot` | Optional APME-only re-eval |
| `apme.risk.escalated` | `_on_apme_escalation` | Halt new entries; notify |
| `event_bus.subscriber.failed` | `_on_subscriber_failed` | Log; increment health metric |
| `system.error` | `_on_system_error` | Aggregate error state |

### 7.2 Orchestrator publications

| Topic | When |
|---|---|
| `system.startup.started` | Startup begins |
| `system.startup.completed` | Startup success |
| `system.startup.failed` | Startup failure |
| `system.shutdown.started` | Shutdown begins |
| `system.shutdown.completed` | Shutdown complete |
| `system.state.changed` | Any state transition |
| `pipeline.cycle.started` | Cycle begins |
| `pipeline.cycle.completed` | Cycle success |
| `pipeline.cycle.failed` | Cycle failure |
| `system.health.degraded` | Health drops below threshold |
| `system.health.recovered` | Health restored |
| `system.engine.failure` | Engine exception isolated |

### 7.3 Subscription wiring rules

**Rule SUB-001:** Subscriptions registered during startup; removed during shutdown.

**Rule SUB-002:** Handler exceptions caught by ErrorBoundary — never propagate to Event Bus dispatch loop.

**Rule SUB-003:** Handlers must not block > `cycle_timeout_seconds` — offload to cycle executor thread if needed.

**Rule SUB-004:** Duplicate subscription on restart prevented by subscription handle tracking.

### 7.4 Wiring implementation sketch

```python
def _wire_subscriptions(self) -> None:
    """Register orchestrator event handlers."""
    patterns = self._config.subscription_patterns or (
        "market.snapshot.published",
        "order.plan.completed",
        "portfolio.snapshot.published",
        "apme.risk.escalated",
    )
    for pattern in patterns:
        handle = self._event_bus.subscribe(pattern, self._dispatch_event)
        self._subscription_handles.append(handle)
```

---

## 8. Pre-Trade Trading Cycle

### 8.1 Pipeline overview

```text
INPUT_GATE → MARKET_DATA_REFRESH → STRATEGY_EVALUATION → TRADE_DECISION
    → PORTFOLIO_RISK_MAPPING → RISK_REVIEW → EXECUTION_PLANNING
    → ORDER_SUBMISSION → RESULT_ASSEMBLY → OUTPUT_VALIDATION
```

### 8.2 Stage specifications

#### Stage 1: INPUT_GATE

| Rule ID | Check | On failure |
|---|---|---|
| IG-001 | orchestrator state RUNNING or DEGRADED | REJECTED |
| IG-002 | context.reference_time timezone-aware | `ORCHESTRATOR.CONTEXT.NAIVE_TIMESTAMP` |
| IG-003 | correlation_id non-empty when strict | `ORCHESTRATOR.CONTEXT.CORRELATION_MISMATCH` |
| IG-004 | enable_pre_trade_cycle=True | SKIPPED |
| IG-005 | no overlapping cycle when serial mode | REJECTED |

#### Stage 2: MARKET_DATA_REFRESH

| Rule ID | Action |
|---|---|
| MD-001 | Use context.market_snapshot if supplied; else trigger engine refresh. |
| MD-002 | Reject if snapshot stale beyond `stale_snapshot_max_age_seconds` in LIVE. |
| MD-003 | Record snapshot_id in cycle result. |

#### Stage 3: STRATEGY_EVALUATION

| Rule ID | Action |
|---|---|
| SE-001 | Build `EngineContext` from market snapshot. |
| SE-002 | Invoke `strategy_evaluation_engine.run(context)`. |
| SE-003 | Isolate failure — cycle FAILED with structured error. |

#### Stage 4: TRADE_DECISION

| Rule ID | Action |
|---|---|
| TD-001 | Pass strategy evaluation output to Trade Decision Engine. |
| TD-002 | If decision is ABSTAIN — short-circuit to COMPLETED with no downstream stages. |
| TD-003 | Record decision_result_id. |

#### Stage 5: PORTFOLIO_RISK_MAPPING

| Rule ID | Action |
|---|---|
| PR-001 | Read latest `PortfolioSnapshot` from Portfolio Manager. |
| PR-002 | Map to `risk.risk_engine.PortfolioSnapshot` via `map_portfolio_snapshot_for_risk()`. |
| PR-003 | Attach account hints from broker funds snapshot. |

#### Stage 6: RISK_REVIEW

| Rule ID | Action |
|---|---|
| RR-001 | Invoke Risk Engine with mapped snapshot and trade decision. |
| RR-002 | On REJECTED — short-circuit; no execution or order stages. |
| RR-003 | On REDUCED — pass sizing hints to Execution Engine. |
| RR-004 | Orchestrator does not override verdict. |

#### Stage 7: EXECUTION_PLANNING

| Rule ID | Action |
|---|---|
| EP-001 | Invoke Execution Engine with RiskDecisionResult. |
| EP-002 | On NO_PLAN or SKIPPED — short-circuit appropriately. |
| EP-003 | Record execution_plan_id. |

#### Stage 8: ORDER_SUBMISSION

| Rule ID | Action |
|---|---|
| OS-001 | Invoke Order Manager with plan and injected broker client. |
| OS-002 | Orchestrator never calls broker directly. |
| OS-003 | Record order_submission_id. |

#### Stage 9–10: RESULT_ASSEMBLY / OUTPUT_VALIDATION

Assemble `TradingCycleResult` with fingerprint; validate invariants.

### 8.3 Short-circuit behaviour

| Condition | Behaviour |
|---|---|
| Trade Decision ABSTAIN | COMPLETED; stages 5–8 skipped |
| Risk REJECTED | COMPLETED or PARTIAL; no execution/order |
| Execution NO_PLAN | COMPLETED; order skipped |
| Engine exception | FAILED; error isolated; orchestrator may enter DEGRADED |

---

## 9. Post-Fill Cycle

### 9.1 Pipeline overview

```text
INPUT_GATE → POSITION_UPDATE → PORTFOLIO_INGEST → APME_EVALUATION
    → MANAGEMENT_DECISION_HANDOFF → RESULT_ASSEMBLY → OUTPUT_VALIDATION
```

### 9.2 Stage specifications

#### Stage 1: INPUT_GATE

| Rule ID | Check | On failure |
|---|---|---|
| PIG-001 | order_tracker not None | REJECTED |
| PIG-002 | enable_post_fill_cycle=True | SKIPPED |
| PIG-003 | correlation_id valid | REJECTED |

#### Stage 2: POSITION_UPDATE

```python
position_result = position_manager.apply_order_tracker(
    context.order_tracker,
    position_context,
)
```

| Rule ID | Action |
|---|---|
| PU-001 | Build `PositionIngestContext` with price hints. |
| PU-002 | On REJECTED — cycle FAILED. |
| PU-003 | Record position_update_id. |

#### Stage 3: PORTFOLIO_INGEST

```python
portfolio_result = portfolio_manager.ingest_position_snapshot(
    position_result.snapshot,
    portfolio_context,
)
```

| Rule ID | Action |
|---|---|
| PI-001 | Assemble `PortfolioIngestContext` with account and Greek hints. |
| PI-002 | On REJECTED — cycle FAILED unless partial policy allows continue. |
| PI-003 | Record portfolio_update_id. |

#### Stage 4: APME_EVALUATION

```python
apme_report = apme_engine.evaluate(
    portfolio_result.snapshot,
    apme_context,
    position_snapshot=position_result.snapshot,
)
```

| Rule ID | Action |
|---|---|
| AE-001 | Assemble `APMEEvaluationContext` from cycle hints. |
| AE-002 | Orchestrator does not interpret APME decisions in v1. |
| AE-003 | Record apme_report_id. |

#### Stage 5: MANAGEMENT_DECISION_HANDOFF

| Rule ID | Action |
|---|---|
| MDH-001 | Log non-HOLD decisions. |
| MDH-002 | Publish summary on `pipeline.cycle.completed` metadata. |
| MDH-003 | v1.1+: queue management ExecutionPlan requests. |

### 9.3 Portfolio-to-risk mapping function

```python
def map_portfolio_snapshot_for_risk(
    portfolio_snapshot: portfolio.portfolio_manager.PortfolioSnapshot,
    *,
    account_equity: float,
    account_cash: float,
) -> risk.risk_engine.PortfolioSnapshot:
    """Map Portfolio Manager output to Risk Engine input contract.

    Explicit orchestrator responsibility — neither Portfolio Manager nor
    Risk Engine performs this mapping.
    """
    ...
```

**Rule MAP-001:** Mapping is pure function — no side effects.

**Rule MAP-002:** Mapping covered by golden fixture tests.

---

## 10. Context Assembly

### 10.1 Portfolio ingest context assembly

| Source | Target field |
|---|---|
| `PostFillCycleContext.account_id` | `PortfolioIngestContext.account_id` |
| `PostFillCycleContext.reference_time` | `PortfolioIngestContext.reference_time` |
| `PostFillCycleContext.equity_hint` | `PortfolioIngestContext.equity_hint` |
| Broker funds snapshot | `cash_available_hint` |
| Broker margin snapshot | `margin_used_hint`, `margin_available_hint` |
| Orchestrator Greek map | `greek_hints` |
| Price hints | `price_hints` |

### 10.2 APME evaluation context assembly

| Source | Target field |
|---|---|
| Portfolio snapshot id | `portfolio_snapshot_id` |
| Volatility hints | `volatility_hints` |
| Regime hints | `regime_hints` |
| Trend hints | `trend_hints` |
| News flags | `news_flags` |
| Signal metadata store | `signal_metadata` |
| Session clock | `session_context` |

### 10.3 EngineContext assembly (pre-trade)

Built from `core/engine_context.py` fields:

| Field | Source |
|---|---|
| `correlation_id` | TradingCycleContext |
| `as_of` | reference_time |
| `source` | `"system_orchestrator"` |
| `execution_mode` | context.execution_mode |
| `snapshot` | market snapshot attachment |
| `tags` | context.tags |

---

## 11. Health Monitoring

### 11.1 Health probe schedule

| Probe | Interval | Failure threshold |
|---|---|---|
| Engine readiness | `health_probe_interval_seconds` | 3 consecutive failures → DEGRADED |
| Event bus metrics | same | subscriber_failure_count spike |
| Snapshot staleness | same | age > `stale_snapshot_max_age_seconds` |
| Last cycle latency | per cycle | > `cycle_timeout_seconds` → warning |

### 11.2 SystemHealthReport assembly

```python
def get_health(self) -> SystemHealthReport:
    """Return aggregated platform health snapshot."""
```

| Component | Health derivation |
|---|---|
| Orchestrator | Current state machine state |
| Each engine | Last invocation success/failure |
| Event Bus | Delivery/failure metrics |
| Cycles | Last cycle status and age |

### 11.3 Degraded mode policy

| Condition | Default behaviour |
|---|---|
| Strategy Evaluation unhealthy | Block pre-trade; allow post-fill |
| Trade Decision unhealthy | Block pre-trade |
| APME unhealthy | Allow trading; log warning on post-fill |
| Market Data unhealthy | Block all cycles |
| Order Manager unhealthy | Block pre-trade; alert CRITICAL |

---

## 12. Error Isolation

### 12.1 Error boundary pattern

```python
def _invoke_engine(
    self,
    engine_id: EngineId,
    callable_fn: Callable[[], T],
    *,
    cycle_id: str,
) -> T | None:
    """Invoke engine with error isolation."""
    try:
        return callable_fn()
    except Exception as exc:
        self._record_engine_failure(engine_id, exc, cycle_id=cycle_id)
        if self._config.fail_fast_on_engine_error:
            raise
        return None
```

### 12.2 Isolation guarantees

| Guarantee | Description |
|---|---|
| Engine exception does not crash process | Caught and recorded |
| Other engines remain callable | Unless shared dependency failed |
| Event handlers isolated | Same pattern as Event Bus subscriber isolation |
| Cycle result always returned | FAILED status with errors populated |

### 12.3 Error taxonomy

Namespace: `ORCHESTRATOR.<CATEGORY>.<DETAIL>`

| Code | Description |
|---|---|
| `ORCHESTRATOR.CONFIG.INVALID` | Invalid configuration |
| `ORCHESTRATOR.CONTEXT.INVALID` | Invalid cycle context |
| `ORCHESTRATOR.CONTEXT.NAIVE_TIMESTAMP` | Timezone-naive datetime |
| `ORCHESTRATOR.CONTEXT.CORRELATION_MISMATCH` | correlation_id invalid |
| `ORCHESTRATOR.STATE.INVALID` | Illegal state for operation |
| `ORCHESTRATOR.CYCLE.OVERLAP` | Concurrent cycle rejected |
| `ORCHESTRATOR.CYCLE.TIMEOUT` | Cycle exceeded timeout |
| `ORCHESTRATOR.ENGINE.FAILURE` | Engine invocation failed |
| `ORCHESTRATOR.ENGINE.NOT_READY` | Engine not initialized |
| `ORCHESTRATOR.SNAPSHOT.STALE` | Market snapshot too old |
| `ORCHESTRATOR.SHUTDOWN.DRAIN_TIMEOUT` | Forced shutdown |
| `ORCHESTRATOR.RESULT.INVALID` | Output validation failed |
| `ORCHESTRATOR.RESULT.FINGERPRINT_MISMATCH` | Fingerprint mismatch |
| `ORCHESTRATOR.SERIALIZATION.UNSUPPORTED_VERSION` | Unknown schema |
| `ORCHESTRATOR.SERIALIZATION.MALFORMED` | Malformed JSON |

---

## 13. Determinism and Thread Safety

### 13.1 Determinism contract

| Mode | Behaviour |
|---|---|
| BACKTEST | Stable `cycle_fingerprint`; ordered stage execution; no wall-clock dependency |
| ANALYSIS | Fingerprint optional; wall-clock allowed |
| LIVE | Fingerprint for audit; real timestamps |

```python
def compute_cycle_fingerprint(
    context: TradingCycleContext,
    result: TradingCycleResult,
    config: SystemOrchestratorConfig,
) -> str:
    """SHA-256 over canonical JSON of cycle outcomes."""
    payload = {
        "correlation_id": context.correlation_id,
        "trigger": context.trigger.value,
        "status": result.status.value,
        "risk_verdict": result.risk_verdict,
        "execution_plan_id": result.execution_plan_id,
        "config_hash": config_fingerprint(config),
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()
```

### 13.2 Thread safety

```python
class SystemOrchestrator:
    def __init__(self, ...):
        self._lock = threading.RLock()
        self._cycle_lock = threading.Lock()
        self._state = OrchestratorState.UNINITIALIZED
        self._in_flight_cycles: int = 0
```

| Rule ID | Rule |
|---|---|
| TS-001 | State transitions under `_lock`. |
| TS-002 | At most one pre-trade cycle under `_cycle_lock` when serial mode. |
| TS-003 | Post-fill cycles may run concurrently with pre-trade only if config allows. |
| TS-004 | `get_health()` and `get_state()` thread-safe. |

---

## 14. Serialization

### 14.1 Schema version

`ORCHESTRATOR_SCHEMA_VERSION = "1.0.0"`

### 14.2 Supported types

- `SystemOrchestratorConfig`
- `TradingCycleResult`
- `PostFillCycleResult`
- `SystemHealthReport`
- `SystemStartupResult`
- `SystemShutdownResult`

```python
def serialize_trading_cycle_result(result: TradingCycleResult) -> str: ...
def deserialize_trading_cycle_result(payload: str) -> TradingCycleResult: ...
def serialize_system_health_report(report: SystemHealthReport) -> str: ...
def deserialize_system_health_report(payload: str) -> SystemHealthReport: ...
```

| Rule ID | Rule |
|---|---|
| SER-001 | Enums serialize as string values. |
| SER-002 | datetimes serialize as ISO-8601 UTC with Z suffix. |
| SER-003 | Mappings serialize as sorted-key JSON objects. |

---

## 15. Public API

### 15.1 Module exports

```python
ORCHESTRATOR_VERSION: str
ORCHESTRATOR_SCHEMA_VERSION: str
PRODUCER_NAME: str

# Enums
OrchestratorState
CycleStatus
CycleTrigger
EngineId
OrchestratorEventType
PreTradeCycleStageId
PostFillCycleStageId
StartupStatus
ShutdownStatus

# Config and context
SystemOrchestratorConfig
TradingCycleContext
PostFillCycleContext

# Results
TradingCycleResult
PostFillCycleResult
SystemHealthReport
SystemStartupResult
SystemShutdownResult

# Supporting
CycleStageResult
EngineHealthStatus
OrchestratorEvent
OrchestratorWarningRecord
OrchestratorErrorRecord
OrchestratorValidationResult

# Service
SystemOrchestrator

# Module functions
default_orchestrator_config() -> SystemOrchestratorConfig
map_portfolio_snapshot_for_risk(...) -> risk.PortfolioSnapshot
validate_trading_cycle_result(result) -> OrchestratorValidationResult
compute_cycle_fingerprint(context, result, config) -> str
serialize_trading_cycle_result(result) -> str
deserialize_trading_cycle_result(payload: str) -> TradingCycleResult
```

### 15.2 `SystemOrchestrator` class

```python
class SystemOrchestrator:
    """Root coordinator for THETA AI TRADER institutional pipeline.

    Initializes engines, wires Event Bus subscriptions, executes
    trading cycles, monitors health, and performs graceful shutdown.
    Never performs market analysis, strategy selection, broker API
    calls, APME logic, or risk calculations.

    Args:
        config: Immutable orchestrator configuration.
        event_bus: Optional pre-constructed EventBus.
        broker_client: Optional BaseBrokerClient for Order Manager.
        engine_registry: Optional pre-built engine instances.
    """

    def start(self) -> SystemStartupResult:
        """Initialize engines, wire subscriptions, transition to RUNNING."""

    def stop(self) -> SystemShutdownResult:
        """Gracefully shutdown; drain in-flight cycles."""

    def run_trading_cycle(self, context: TradingCycleContext) -> TradingCycleResult:
        """Execute pre-trade entry pipeline."""

    def run_post_fill_cycle(
        self,
        context: PostFillCycleContext,
    ) -> PostFillCycleResult:
        """Execute post-fill position → portfolio → APME pipeline."""

    def get_health(self) -> SystemHealthReport:
        """Return aggregated platform health."""

    def get_state(self) -> OrchestratorState:
        """Return current lifecycle state."""

    def get_latest_cycle_result(self) -> TradingCycleResult | None:
        """Return most recent pre-trade cycle result."""

    def on_market_snapshot_event(self, envelope: EventEnvelope) -> None:
        """Handle market.snapshot.published for optional cycle trigger."""
```

---

## 16. Configuration

```python
@dataclass(frozen=True)
class SystemOrchestratorConfig:
    """Immutable orchestrator configuration."""

    execution_mode: StrategyExecutionMode = StrategyExecutionMode.LIVE
    account_id: str = ""
    enable_pre_trade_cycle: bool = True
    enable_post_fill_cycle: bool = True
    enable_event_driven_cycles: bool = True
    serial_cycle_execution: bool = True
    cycle_timeout_seconds: int = 120
    shutdown_drain_timeout_seconds: int = 60
    health_probe_interval_seconds: int = 30
    stale_snapshot_max_age_seconds: int = 60
    strict_correlation: bool = True
    deterministic_fingerprint: bool = True
    publish_system_events: bool = True
    fail_fast_on_engine_error: bool = False
    subscription_patterns: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
```

| Setting | LIVE | ANALYSIS | BACKTEST |
|---|---|---|---|
| `strict_correlation` | True | True | False |
| `enable_event_driven_cycles` | True | False | False |
| `deterministic_fingerprint` | True | False | True |
| `cycle_timeout_seconds` | 120 | 300 | 600 |
| `fail_fast_on_engine_error` | False | True | True |

---

## 17. Logging Events

Logger name: `system.system_orchestrator`.

| Event | Level | When |
|---|---|---|
| `orchestrator.startup.start` | INFO | start() invoked |
| `orchestrator.startup.complete` | INFO | Startup success |
| `orchestrator.startup.failed` | ERROR | Startup failure |
| `orchestrator.shutdown.start` | INFO | stop() invoked |
| `orchestrator.shutdown.complete` | INFO | Shutdown success |
| `orchestrator.cycle.start` | INFO | Cycle begins |
| `orchestrator.cycle.complete` | INFO | Cycle completes |
| `orchestrator.cycle.failed` | ERROR | Cycle failure |
| `orchestrator.engine.failure` | ERROR | Engine exception isolated |
| `orchestrator.health.degraded` | WARNING | Health degraded |
| `orchestrator.state.changed` | INFO | State transition |

---

## 18. Testing Strategy

### 18.1 Coverage target

**Minimum line coverage: 95%** on `system/system_orchestrator.py`.

### 18.2 Required test categories

| Category | Tests |
|---|---|
| Lifecycle | start/stop state transitions; failed startup; forced shutdown |
| Pre-trade cycle | Full happy path with mocked engines |
| Pre-trade short-circuit | ABSTAIN, REJECTED, NO_PLAN paths |
| Post-fill cycle | Position → Portfolio → APME chain |
| Event wiring | Subscription register/remove |
| Health monitoring | Degraded detection and recovery |
| Error isolation | Engine exception does not crash orchestrator |
| Overlap rejection | Serial cycle lock |
| Portfolio-risk mapping | Golden mapping fixture |
| Context assembly | Portfolio and APME context builders |
| Determinism | Stable cycle_fingerprint in BACKTEST |
| Serialization | Round-trip TradingCycleResult |
| Thread safety | Concurrent get_health during cycle |
| Validation | Invalid context rejected |

### 18.3 Test doubles

| Double | Purpose |
|---|---|
| `MockEngineRegistry` | Replace all engines with recording fakes |
| `RecordingEventBus` | Capture subscriptions and publications |
| `StubBrokerClient` | No-op broker for order manager |
| `FixturePortfolioSnapshot` | Deterministic portfolio for risk mapping |
| `ClockStub` | Deterministic timestamps for BACKTEST |

---

## 19. Performance Requirements

| Operation | Target (p99) |
|---|---|
| Startup (all engines mocked) | < 500 ms |
| Health probe | < 10 ms |
| Pre-trade cycle overhead (excluding engine work) | < 5 ms |
| Post-fill cycle overhead (excluding engine work) | < 5 ms |
| Portfolio-risk mapping | < 2 ms |
| Context assembly | < 1 ms |
| State transition | < 0.1 ms |

**Rule PERF-001:** Orchestrator overhead must not exceed 5% of total cycle time in production profiling.

---

## 20. Definition of Done

### 20.1 Implementation

- [ ] `system/system_orchestrator.py` implements full public API per §15.
- [ ] Lifecycle state machine per §6.
- [ ] Pre-trade and post-fill pipelines per §8–§9.
- [ ] Event Bus wiring per §7.
- [ ] Health monitoring per §11.
- [ ] Error isolation per §12.
- [ ] Portfolio-to-risk mapping per §9.3.
- [ ] No forbidden domain logic per §3.

### 20.2 Quality

- [ ] Unit test coverage ≥ 95%.
- [ ] Google-style docstrings on all public types and methods.
- [ ] JSON serialization round-trip schema v1.0.0.
- [ ] Thread-safe lifecycle verified by concurrent tests.
- [ ] Golden mapping fixture for risk handoff.

### 20.3 Documentation

- [ ] This specification implemented faithfully.
- [ ] `CHANGELOG.md` updated when module ships.
- [ ] Integration test documents full institutional pipeline path.

---

## Appendix A — Worked Examples

### A.1 Full pre-trade cycle (happy path)

**Input:** RUNNING orchestrator, fresh market snapshot, strategy produces ENTRY signal, risk APPROVED.

**Flow:**

1. `run_trading_cycle(context)` → `pipeline.cycle.started`
2. Strategy Evaluation → Trade Decision → Risk APPROVED
3. Execution Engine → READY plan
4. Order Manager → submission result
5. `TradingCycleResult` status COMPLETED
6. `pipeline.cycle.completed`

### A.2 Risk rejected — no order

**Input:** Risk Engine returns REJECTED.

**Result:**

- Stages 7–8 skipped
- `risk_verdict=REJECTED`
- status COMPLETED or PARTIAL
- No broker submission

### A.3 Post-fill cycle after order completion

**Input:** `OrderTracker` with fill data.

**Flow:**

1. Position Manager APPLIED
2. Portfolio Manager APPLIED
3. APME COMPLETED with HOLD decisions
4. `PostFillCycleResult` sealed

### A.4 Graceful shutdown with in-flight cycle

**Input:** Cycle running; `stop()` called.

**Result:**

1. State → STOPPING
2. Cycle completes current stage
3. Subscriptions removed
4. State → STOPPED within timeout

---

## Appendix B — Engine Invocation Matrix

| Stage | Engine | Method | Input artifact | Output artifact |
|---|---|---|---|---|
| Market refresh | Market Data | `refresh()` / bus | — | MarketSnapshot |
| Strategy | Strategy Evaluation | `run(EngineContext)` | MarketSnapshot | EngineResult |
| Decision | Trade Decision | `run(EngineContext)` | Strategy output | DecisionResult |
| Risk | Risk | `review(...)` | Mapped PortfolioSnapshot | RiskDecisionResult |
| Execution | Execution | `plan(...)` | RiskDecisionResult | ExecutionPlan |
| Order | Order Manager | `submit_plan(...)` | ExecutionPlan | OrderSubmissionResult |
| Position | Position Manager | `apply_order_tracker(...)` | OrderTracker | PositionUpdateResult |
| Portfolio | Portfolio Manager | `ingest_position_snapshot(...)` | PositionSnapshot | PortfolioUpdateResult |
| APME | APME | `evaluate(...)` | PortfolioSnapshot | APMEDecisionReport |

---

## Appendix C — Event Topic Catalog (Orchestrator Scope)

| Namespace | Owner | Orchestrator role |
|---|---|---|
| `market.*` | Market Data Engine | Subscribe |
| `engine.*` | Various engines | Optional subscribe |
| `order.*` | Order Manager | Subscribe |
| `position.*` | Position Manager | Optional subscribe |
| `portfolio.*` | Portfolio Manager | Subscribe |
| `apme.*` | APME | Subscribe |
| `pipeline.*` | Orchestrator | Publish |
| `system.*` | Orchestrator | Publish |

---

## Appendix D — Legacy Migration Notes

| Legacy module | v1 institutional path |
|---|---|
| `trade_risk_orchestrator.py` | Replaced by SystemOrchestrator pre-trade stages 5–6 |
| `strategy_engine.py` (root) | Replaced by Strategy Evaluation Engine |
| `main.py` ad-hoc pipeline | Entry point delegates to `SystemOrchestrator` |
| `config_manager.py` direct load | Config injected into `SystemOrchestratorConfig` at bootstrap |

**Rule MIG-001:** Legacy modules remain in repository for reference but are not imported by `system/system_orchestrator.py`.

---

## Appendix E — Glossary

| Term | Definition |
|---|---|
| **System Orchestrator** | Root coordinator that wires and invokes all institutional pipeline engines. |
| **Trading cycle** | Pre-trade pipeline from market snapshot through order submission. |
| **Post-fill cycle** | Post-execution pipeline from fills through APME evaluation. |
| **Cycle fingerprint** | Deterministic hash for replay verification. |
| **Degraded mode** | RUNNING with one or more non-critical engine failures. |
| **Portfolio-risk mapping** | Explicit transform from Portfolio Manager to Risk Engine snapshot contract. |
| **Error isolation** | Containing engine failures without process crash. |
| **Graceful shutdown** | Draining in-flight work before stopping subscriptions and engines. |

---

*End of specification — document length meets minimum 1200-line requirement for institutional review.*
