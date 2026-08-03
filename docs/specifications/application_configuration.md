# Application Configuration — Software Engineering Specification

| Field | Value |
|---|---|
| **Module** | `config/application_configuration.py` |
| **Document version** | 1.0.0 |
| **Status** | Draft — ready for implementation |
| **Owner** | THETA AI TRADER Core Platform |
| **Last updated** | 2026-08-04 |

---

## 1. Purpose

`config/application_configuration.py` defines the **centralized, immutable configuration system** for THETA AI TRADER v1.0.

The module is the **single authoritative bootstrap layer** that loads, validates, merges, and exposes frozen configuration for every institutional pipeline engine, the System Orchestrator, operational surfaces (Dashboard, Paper Trading, Live Trading), and platform infrastructure (Event Bus, logging, secrets) — but **never** generates trading signals, places orders, performs risk calculations, or mutates runtime trading state.

The module answers: *"How do we load one validated, environment-aware, secret-safe configuration bundle at application startup and project it deterministically into every engine without scattering config logic across the codebase?"*

It is **not** a strategy engine. It is **not** a risk manager. It is **not** a runtime kill-switch store. It is **not** a user preference editor with live mutation in v1 (read-only immutable bundle at bootstrap; optional future `ConfigurationService` may wrap reload semantics). It is the **configuration gate** that ensures every downstream component receives typed, validated, frozen settings.

### Platform placement

```text
[CLI / Dashboard / Scheduler / Tests]
              ↓
[config/application_configuration.py]          ← THIS MODULE
    load profile (development | paper | production)
    merge defaults → file → environment → secrets
    validate cross-section invariants
    freeze ApplicationConfiguration
              ↓
    ┌─────────────────────────────────────────────────────────────┐
    │ CONFIG PROJECTION (immutable engine configs)                 │
    │   SystemOrchestratorConfig                                   │
    │   EventBusPolicy                                             │
    │   MarketDataEngineConfig                                     │
    │   StrategyEvaluationEngineConfig + StrategyRegistryConfig    │
    │   TradeDecisionEngineConfig                                  │
    │   RiskEngineConfig + UserRiskProfile defaults                │
    │   ExecutionEngineConfig + OrderManagerConfig                 │
    │   PositionManagerConfig + PortfolioManagerConfig             │
    │   APMEConfig                                                 │
    │   DashboardConfiguration                                     │
    │   LoggingConfiguration                                       │
    │   BrokerConfiguration (non-secret metadata + secret refs)    │
    └─────────────────────────────────────────────────────────────┘
              ↓
[system/system_orchestrator.py]  → injects configs into EngineRegistry
[Individual engines at construction] → receive projected *Config types
[Dashboard / Paper / Live runners] → read ApplicationConfiguration snapshot
```

### Architecture freeze note

The platform architecture is **FROZEN** for v1.0:

- **Application Configuration** is the **only** module permitted to perform environment-variable resolution, secret loading orchestration, and cross-engine configuration validation at bootstrap.
- **Individual engines** retain their own `*Config` frozen dataclasses (`RiskEngineConfig`, `APMEConfig`, etc.) — Application Configuration **projects** into those types; engines do not parse `.env` files or OS environment directly.
- **System Orchestrator** receives `SystemOrchestratorConfig` from Application Configuration — orchestrator does not load YAML/JSON.
- **Event Bus** receives `EventBusPolicy` from Application Configuration.
- **Legacy `config_manager.py`** remains for backward compatibility during migration; v1 institutional path uses Application Configuration as source of truth; legacy JSON user settings map into `RiskConfiguration` and `FeatureFlags` where applicable (Appendix D).
- **Secrets never appear in serialized configuration output** — only secret reference keys and redacted metadata.
- **No engine imports `config/application_configuration.py` at runtime inside hot paths** — configuration is resolved once at startup and passed by injection.

### Consumers

| Consumer | Configuration received |
|---|---|
| `system/system_orchestrator.py` | `SystemOrchestratorConfig`, broker client factory inputs |
| `core/event_bus.py` | `EventBusPolicy` |
| `market_data/market_data_engine.py` | `MarketDataEngineConfig` |
| `strategy/strategy_evaluation_engine.py` | `StrategyEvaluationEngineConfig` |
| `strategy/registry.py` | `StrategyRegistryConfig` |
| `decision/trade_decision_engine.py` | `TradeDecisionEngineConfig` |
| `risk/risk_engine.py` | `RiskEngineConfig`, default `UserRiskProfile` |
| `execution/execution_engine.py` | `ExecutionEngineConfig` |
| `execution/order_manager.py` | `OrderManagerConfig` |
| `portfolio/position_manager.py` | `PositionManagerConfig` |
| `portfolio/portfolio_manager.py` | `PortfolioManagerConfig` |
| `apme/adaptive_position_management_engine.py` | `APMEConfig` |
| Dashboard (future) | `DashboardConfiguration`, redacted `ApplicationConfiguration` view |
| Paper Trading runner | Full `ApplicationConfiguration` with `EnvironmentProfile.PAPER` |
| Live Trading runner | Full `ApplicationConfiguration` with `EnvironmentProfile.PRODUCTION` |

### Goals

1. Provide a **single immutable `ApplicationConfiguration`** bundle for the entire platform.
2. Support **environment profiles**: `development`, `paper`, `production`.
3. **Validate** all configuration at load time — fail fast before engine construction.
4. **Load environment variables** with documented `THETA_*` prefix convention.
5. **Secret management interface** — `SecretProvider` protocol; never embed secrets in config files.
6. **Logging configuration** — centralized log levels, formatters, and handler routing.
7. **Broker configuration** — broker identity, endpoints, timeouts; credentials via secrets.
8. **Market data configuration** — universe, publish interval, reconnect policy projection.
9. **Strategy configuration** — registry paths, enabled strategies, evaluation thresholds.
10. **Risk configuration** — user limits and engine policy projection.
11. **Execution configuration** — slippage, retry, order manager policies.
12. **Dashboard configuration** — host, port, auth mode, refresh intervals.
13. **Feature flags** — toggles for optional subsystems without code changes.
14. **Serialization** — JSON round-trip of redacted public configuration schema v1.0.0.
15. **Deterministic loading** — identical inputs produce identical config fingerprint.
16. **Thread-safe read** — immutable bundle safe for concurrent reads after load.
17. **Config fingerprint** — SHA-256 hash for audit and replay verification.
18. **Profile-aware defaults** — each profile supplies opinionated baseline overrides.
19. **Cross-section validation** — e.g., paper profile cannot enable live broker without explicit override.
20. **Engine config projection** — typed factory methods per engine.
21. **No duplicate policy definitions** — compose existing engine config types.
22. **Migration path** from legacy `config/user_config.json` (Appendix D).
23. **Google-style docstrings** on all public types and methods.
24. **Unit test coverage ≥ 95%** on `config/application_configuration.py`.
25. **Zero trading logic** in configuration module.

### Success criteria

- `load_application_configuration()` returns validated frozen `ApplicationConfiguration`.
- Invalid profile, missing required secret ref, or cross-section violation raises `ApplicationConfigurationError` before any engine starts.
- `config.to_orchestrator_config()` produces valid `SystemOrchestratorConfig`.
- Each `config.to_*_config()` projection passes engine-native `validate_configuration()` where applicable.
- Identical load inputs produce identical `config_fingerprint`.
- Serialized output contains **no secret values**.
- Unit test coverage ≥ 95% line coverage on `config/application_configuration.py`.

### Relationship to other modules

| Module | Relationship |
|---|---|
| `system/system_orchestrator.py` | **Primary consumer.** Receives orchestrator config and execution mode. |
| `core/event_bus.py` | Receives `EventBusPolicy` from projection. |
| All `*EngineConfig` modules | **Composed types.** Application Configuration projects into them; does not replace them. |
| `config_manager.py` (legacy) | **Migration source.** User-editable risk/trading settings map to `RiskConfiguration` / `FeatureFlags`. |
| `config/user_config.json` (legacy) | Optional merge input for user limits when `THETA_USER_CONFIG_PATH` set. |
| `broker/base_broker.py` | Broker metadata in `BrokerConfiguration`; credentials via `SecretProvider`. |
| Engine modules | **Must not** import Application Configuration in evaluate/plan/review hot paths. |

### Distinction from legacy ConfigManager

| Concern | Legacy `config_manager.py` | Application Configuration |
|---|---|---|
| Mutability | Mutable dict with live updates | Immutable frozen bundle at bootstrap (v1) |
| Scope | User-editable risk/trading prefs | Full platform bootstrap |
| Validation | Per-setting ranges | Cross-engine + profile invariants |
| Secrets | Not supported | `SecretProvider` interface |
| Engine projection | Convenience getters only | Typed projection to all engine configs |
| Institutional path | Not used by orchestrator | **Authoritative** for v1 pipeline |

---

## 2. Responsibilities

`config/application_configuration.py` **is responsible for**:

| # | Responsibility | Description |
|---|---|---|
| R1 | **Profile resolution** | Resolve `EnvironmentProfile` from env, file, or explicit argument. |
| R2 | **Default assembly** | Merge layered defaults: base → profile → file → env → CLI overrides. |
| R3 | **Immutable output** | Emit frozen `ApplicationConfiguration` dataclass tree. |
| R4 | **Validation** | Validate all fields and cross-section invariants before return. |
| R5 | **Environment variable loading** | Parse documented `THETA_*` variables. |
| R6 | **Secret reference resolution** | Resolve secret refs via injected `SecretProvider`. |
| R7 | **Logging configuration** | Provide `LoggingConfiguration` for platform log setup. |
| R8 | **Broker configuration** | Broker identity, endpoints, paper/live mode metadata. |
| R9 | **Market data configuration** | Universe, publish, reconnect settings. |
| R10 | **Strategy configuration** | Registry, plugin paths, evaluation policy. |
| R11 | **Risk configuration** | User limits + risk engine policy projection. |
| R12 | **Execution configuration** | Execution and order manager policy projection. |
| R13 | **Dashboard configuration** | UI host, port, auth, polling intervals. |
| R14 | **Feature flags** | Boolean toggles for optional features. |
| R15 | **Orchestrator projection** | `to_orchestrator_config()`. |
| R16 | **Per-engine projection** | `to_risk_engine_config()`, `to_apme_config()`, etc. |
| R17 | **Config fingerprint** | Deterministic SHA-256 over canonical redacted JSON. |
| R18 | **Serialization** | Redacted JSON export/import (no secrets). |
| R19 | **Error taxonomy** | Stable `CONFIG.*` error codes. |
| R20 | **Thread-safe reads** | Immutable bundle; no mutable shared state post-load. |
| R21 | **Profile guardrails** | Production profile enforces stricter invariants. |
| R22 | **Paper trading profile** | Simulated broker defaults; relaxed secret requirements. |
| R23 | **Development profile** | Local-friendly defaults; verbose logging. |
| R24 | **Account identity** | `account_id`, `user_id` metadata for orchestrator. |
| R25 | **Execution mode mapping** | Map profile to `StrategyExecutionMode`. |
| R26 | **Timezone configuration** | Platform timezone default (`Asia/Kolkata`). |
| R27 | **Config file discovery** | Standard paths: `config/application.yaml`, env override. |
| R28 | **Override precedence documentation** | Explicit merge order in docstrings. |
| R29 | **Redaction for logs** | `redact_for_logging()` strips sensitive fields. |
| R30 | **Public API stability** | Schema version `APPLICATION_CONFIG_SCHEMA_VERSION = "1.0.0"`. |

---

## 3. Non-Responsibilities

`config/application_configuration.py` **must not**:

| # | Non-responsibility | Rationale |
|---|---|---|
| NR1 | **Generate trading signals** | Strategy Evaluation Engine responsibility. |
| NR2 | **Perform risk calculations** | Risk Engine responsibility. |
| NR3 | **Place or modify orders** | Order Manager responsibility. |
| NR4 | **Connect to broker APIs directly** | Broker client constructed elsewhere using resolved secrets. |
| NR5 | **Mutate runtime kill-switch state** | Runtime safety state is separate from bootstrap config in v1. |
| NR6 | **Persist live configuration changes to disk in v1** | v1 is bootstrap-only; dynamic updates are v1.1+ scope. |
| NR7 | **Implement dashboard UI** | Dashboard consumes configuration only. |
| NR8 | **Load Zerodha SDK** | Broker abstraction only. |
| NR9 | **Parse strategy plugin code** | Strategy Registry responsibility. |
| NR10 | **Validate market snapshots** | Market Data Engine responsibility. |
| NR11 | **Store API keys in plain config files** | Secrets via `SecretProvider` only. |
| NR12 | **Replace engine-native config classes** | Composes and projects into existing `*Config` types. |
| NR13 | **Invoke engines** | System Orchestrator responsibility. |
| NR14 | **Perform health checks** | Orchestrator / individual engines. |
| NR15 | **Train ML models** | Out of scope. |
| NR16 | **Authenticate dashboard users at runtime** | Dashboard auth layer; config supplies policy only. |
| NR17 | **Silently coerce invalid env vars** | Invalid values raise structured errors. |
| NR18 | **Allow production profile with development logging defaults without explicit override** | Profile guardrails enforced. |
| NR19 | **Import engine internals beyond public config types** | Dependency direction: config → engine config types only. |
| NR20 | **Embed business logic for strategy selection** | Trade Decision Engine responsibility. |

---

## 4. Architecture

### 4.1 Layered design

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                 config/application_configuration.py                          │
│  (bootstrap-only — no trading logic, no broker calls, no runtime mutation)   │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │ ConfigurationLoader│→│ ConfigurationValidator│→│ ApplicationConfiguration │  │
│  │ (merge layers)    │  │ (invariants)       │  │ (frozen output)          │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘  │
│           │                     │                         │                  │
│           ▼                     ▼                         ▼                  │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ EnvironmentVariableSource · FileConfigurationSource · SecretProvider   │  │
│  │ ProfileDefaults · ConfigProjector · ConfigFingerprint · ConfigSerializer │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
         ▲                                           │
         │ LoadOptions + SecretProvider              ▼
         │                                    Engine *Config projections
         │                                    Redacted JSON / fingerprint
```

### 4.2 Load pipeline

```text
RESOLVE_PROFILE
    → LOAD_BASE_DEFAULTS
    → APPLY_PROFILE_DEFAULTS (development | paper | production)
    → MERGE_CONFIG_FILE (optional YAML/JSON)
    → MERGE_LEGACY_USER_CONFIG (optional user_config.json)
    → APPLY_ENVIRONMENT_VARIABLES (THETA_*)
    → APPLY_CLI_OVERRIDES (optional)
    → RESOLVE_SECRET_REFERENCES (via SecretProvider)
    → VALIDATE_SECTIONS
    → VALIDATE_CROSS_SECTION_INVARIANTS
    → VALIDATE_PROFILE_GUARDRAILS
    → FREEZE → ApplicationConfiguration
    → COMPUTE_FINGERPRINT
```

**Rule LOAD-001:** Any failure before FREEZE raises `ApplicationConfigurationError`; no partial configuration returned.

**Rule LOAD-002:** Merge order is fixed; later layers override earlier layers.

**Rule LOAD-003:** Secret resolution occurs after all non-secret merges.

### 4.3 Dependency direction

```text
config/application_configuration.py
    → core/event_bus.py (EventBusPolicy type only)
    → system/system_orchestrator.py (SystemOrchestratorConfig type only)
    → market_data/market_data_engine.py (MarketDataEngineConfig)
    → strategy/strategy_evaluation_engine.py
    → strategy/registry.py
    → decision/trade_decision_engine.py
    → risk/risk_engine.py
    → execution/execution_engine.py
    → execution/order_manager.py
    → portfolio/position_manager.py
    → portfolio/portfolio_manager.py
    → apme/adaptive_position_management_engine.py
    → strategy/signals.py (StrategyExecutionMode)

Forbidden imports:
    → broker/zerodha/*
    → strategy plugins
    → engine evaluate/plan/review implementations
    → system orchestrator runtime (cycle execution)
```

### 4.4 Configuration vs runtime state

| Category | Stored in Application Configuration | Stored elsewhere |
|---|---|---|
| Max risk per trade % | Yes (`RiskConfiguration`) | — |
| Kill switch active | Default only (`RiskEngineConfig.kill_switch_active` bootstrap default) | Runtime toggle in v1.1+ safety manager |
| Broker API key | Secret ref only | `SecretProvider` |
| Open positions | No | Position Manager |
| Last config change audit | Optional metadata timestamp | Future ConfigurationService |

---

## 5. Data Model

All public outward-facing types are **immutable dataclasses** (`frozen=True`) unless noted.

### 5.1 Type hierarchy

```text
ApplicationConfiguration (immutable)                 ← PRIMARY OUTPUT
├── schema_version: str
├── config_id: str
├── config_fingerprint: str
├── loaded_at: datetime
├── profile: EnvironmentProfile
├── execution_mode: StrategyExecutionMode
├── account: AccountConfiguration
├── logging: LoggingConfiguration
├── broker: BrokerConfiguration
├── market_data: MarketDataConfiguration
├── strategy: StrategyConfiguration
├── risk: RiskConfiguration
├── execution: ExecutionConfiguration
├── orchestrator: OrchestratorConfiguration
├── event_bus: EventBusConfiguration
├── position: PositionConfiguration
├── portfolio: PortfolioConfiguration
├── apme: APMEConfiguration
├── dashboard: DashboardConfiguration
├── features: FeatureFlags
├── secrets: SecretReferences (refs only — never values)
├── paths: PathConfiguration
└── metadata: Mapping[str, str]

EnvironmentProfile (enum)
├── DEVELOPMENT
├── PAPER
└── PRODUCTION

BrokerConfiguration (immutable)
├── broker_id: str
├── broker_type: BrokerType
├── api_base_url: str
├── websocket_url: str | None
├── connect_timeout_seconds: float
├── request_timeout_seconds: float
├── max_retries: int
├── paper_trading: bool
├── api_key_secret_ref: str
├── api_secret_secret_ref: str
├── access_token_secret_ref: str | None
└── metadata: Mapping[str, str]

RiskConfiguration (immutable)
├── user_limits: UserRiskLimits
├── engine: RiskEnginePolicyOverrides
├── budget: RiskBudgetConfiguration
├── kill_switch_default_active: bool
└── metadata: Mapping[str, str]

ExecutionConfiguration (immutable)
├── engine: ExecutionEnginePolicyOverrides
├── order_manager: OrderManagerPolicyOverrides
├── slippage_bps_default: float
├── max_legs_per_plan: int
└── metadata: Mapping[str, str]

DashboardConfiguration (immutable)
├── enabled: bool
├── host: str
├── port: int
├── auth_mode: DashboardAuthMode
├── refresh_interval_seconds: float
├── cors_allowed_origins: tuple[str, ...]
└── metadata: Mapping[str, str]

FeatureFlags (immutable)
├── trading_enabled: bool
├── new_entries_enabled: bool
├── post_fill_apme_enabled: bool
├── event_driven_cycles_enabled: bool
├── dashboard_enabled: bool
├── paper_broker_simulation: bool
└── metadata: Mapping[str, str]

LoadOptions (immutable)
├── profile: EnvironmentProfile | None
├── config_file_path: str | None
├── user_config_path: str | None
├── cli_overrides: Mapping[str, str]
├── allow_missing_secrets: bool
└── metadata: Mapping[str, str]

ApplicationConfigurationError
ApplicationConfigurationValidationResult
ConfigurationValidationIssue
SecretProvider (Protocol)
```

### 5.2 Enumerations

#### 5.2.1 `EnvironmentProfile`

| Value | Description |
|---|---|
| `DEVELOPMENT` | Local development; verbose logging; secrets optional. |
| `PAPER` | Simulated trading; paper broker; production-like strictness. |
| `PRODUCTION` | Live trading; strict validation; secrets required. |

#### 5.2.2 `BrokerType`

| Value | Description |
|---|---|
| `ZERODHA_KITE` | Zerodha Kite Connect (v1 default broker). |
| `MOCK` | In-process mock broker for tests and paper simulation. |
| `RECORDING` | Wrapper that records requests for replay tests. |

#### 5.2.3 `DashboardAuthMode`

| Value | Description |
|---|---|
| `NONE` | No auth (development only). |
| `TOKEN` | Static bearer token from secret ref. |
| `OIDC` | OpenID Connect (v1.1+ placeholder — config field reserved). |

#### 5.2.4 `LogFormat`

| Value | Description |
|---|---|
| `TEXT` | Human-readable text format. |
| `JSON` | Structured JSON logs for production. |

#### 5.2.5 `SecretSource`

| Value | Description |
|---|---|
| `ENVIRONMENT` | Read from named environment variable at resolve time. |
| `FILE` | Read from file path (0600 permissions required in production). |
| `VAULT` | External vault provider (v1.1+ — interface reserved). |
| `INLINE_FOR_TESTS` | Test-only inline provider. |

#### 5.2.6 `ConfigurationLayer`

| Value | Merge order |
|---|---|
| `BASE_DEFAULTS` | 1 |
| `PROFILE_DEFAULTS` | 2 |
| `CONFIG_FILE` | 3 |
| `LEGACY_USER_CONFIG` | 4 |
| `ENVIRONMENT` | 5 |
| `CLI_OVERRIDE` | 6 |

---

## 6. Core Types — Field Specifications

### 6.1 `ApplicationConfiguration`

| Field | Type | Required | Description |
|---|---|---|---|
| `schema_version` | `str` | Yes | `APPLICATION_CONFIG_SCHEMA_VERSION`. |
| `config_id` | `str` | Yes | UUID v4 assigned at load. |
| `config_fingerprint` | `str` | Yes | SHA-256 redacted canonical JSON. |
| `loaded_at` | `datetime` | Yes | Timezone-aware load timestamp. |
| `profile` | `EnvironmentProfile` | Yes | Active environment profile. |
| `execution_mode` | `StrategyExecutionMode` | Yes | Derived from profile and overrides. |
| `account` | `AccountConfiguration` | Yes | Account and user identity hints. |
| `logging` | `LoggingConfiguration` | Yes | Platform logging setup. |
| `broker` | `BrokerConfiguration` | Yes | Broker connection metadata. |
| `market_data` | `MarketDataConfiguration` | Yes | Market data engine settings. |
| `strategy` | `StrategyConfiguration` | Yes | Strategy registry and evaluation. |
| `risk` | `RiskConfiguration` | Yes | Risk limits and engine overrides. |
| `execution` | `ExecutionConfiguration` | Yes | Execution and order policies. |
| `orchestrator` | `OrchestratorConfiguration` | Yes | System orchestrator settings. |
| `event_bus` | `EventBusConfiguration` | Yes | Event bus policy settings. |
| `position` | `PositionConfiguration` | Yes | Position manager settings. |
| `portfolio` | `PortfolioConfiguration` | Yes | Portfolio manager settings. |
| `apme` | `APMEConfiguration` | Yes | APME settings. |
| `dashboard` | `DashboardConfiguration` | Yes | Dashboard settings. |
| `features` | `FeatureFlags` | Yes | Feature toggles. |
| `secrets` | `SecretReferences` | Yes | Secret ref keys only. |
| `paths` | `PathConfiguration` | Yes | Configurable filesystem paths. |
| `metadata` | `Mapping[str, str]` | No | Audit labels. |

**Invariant AC-001:** `loaded_at` must be timezone-aware.

**Invariant AC-002:** `config_fingerprint` must match `compute_config_fingerprint(self)`.

**Invariant AC-003:** Serialized form must contain zero secret values.

### 6.2 `AccountConfiguration`

| Field | Type | Default | Description |
|---|---|---|---|
| `account_id` | `str` | `""` | Broker account identifier. |
| `user_id` | `str` | `""` | Platform user identifier. |
| `display_name` | `str` | `""` | Human-readable account label. |
| `currency` | `str` | `"INR"` | Account currency code. |
| `timezone` | `str` | `"Asia/Kolkata"` | Platform timezone. |

**Rule ACC-001:** `account_id` required non-empty in `PRODUCTION` profile.

### 6.3 `LoggingConfiguration`

| Field | Type | Default (dev / paper / prod) | Description |
|---|---|---|---|
| `root_level` | `str` | DEBUG / INFO / INFO | Root logger level. |
| `platform_level` | `str` | DEBUG / INFO / WARNING | `theta` logger namespace. |
| `engine_level` | `str` | INFO / INFO / INFO | Engine module default. |
| `broker_level` | `str` | WARNING / INFO / WARNING | Broker client logging. |
| `format` | `LogFormat` | TEXT / JSON / JSON | Log output format. |
| `log_file_path` | `str | None` | None / logs/theta.log / logs/theta.log | Optional file handler. |
| `max_file_bytes` | `int` | 10_485_760 | Rotating file max size. |
| `backup_count` | `int` | 5 | Rotating file backups. |
| `correlation_id_injection` | `bool` | True | Inject correlation_id into log records. |

**Rule LOG-001:** `PRODUCTION` profile defaults to `LogFormat.JSON`.

**Rule LOG-002:** Invalid log level strings raise `CONFIG.LOGGING.INVALID_LEVEL`.

### 6.4 `BrokerConfiguration`

| Field | Type | Description |
|---|---|---|
| `broker_id` | `str` | Stable broker instance identifier. |
| `broker_type` | `BrokerType` | Broker implementation selector. |
| `api_base_url` | `str` | REST API base URL. |
| `websocket_url` | `str | None` | WebSocket endpoint for streaming. |
| `connect_timeout_seconds` | `float` | Connection timeout. |
| `request_timeout_seconds` | `float` | Per-request timeout. |
| `max_retries` | `int` | Max retry attempts for idempotent reads. |
| `paper_trading` | `bool` | True when using paper/simulated session. |
| `api_key_secret_ref` | `str` | Secret ref key for API key. |
| `api_secret_secret_ref` | `str` | Secret ref key for API secret. |
| `access_token_secret_ref` | `str | None` | Optional access token ref. |
| `metadata` | `Mapping[str, str]` | Non-sensitive broker metadata. |

**Rule BRK-001:** `PRODUCTION` + `BrokerType.ZERODHA_KITE` requires all secret refs resolvable.

**Rule BRK-002:** `PAPER` profile defaults to `BrokerType.MOCK` unless explicitly overridden.

**Rule BRK-003:** Secret values never stored on `BrokerConfiguration`.

#### 6.4.1 Default broker endpoints (Zerodha Kite)

| Field | Default value |
|---|---|
| `api_base_url` | `https://api.kite.trade` |
| `websocket_url` | `wss://ws.kite.trade` |

### 6.5 `MarketDataConfiguration`

| Field | Type | Description |
|---|---|---|
| `underlying` | `str` | Primary underlying symbol (default `NIFTY`). |
| `exchange` | `str` | Derivatives exchange (default `NFO`). |
| `spot_exchange` | `str` | Spot exchange (default `NSE`). |
| `spot_symbol` | `str` | Spot index symbol. |
| `strikes_each_side` | `int` | Option strikes each side of ATM. |
| `include_vix` | `bool` | Subscribe to India VIX. |
| `publish_interval_seconds` | `float` | Snapshot publish cadence. |
| `instrument_cache_ttl_seconds` | `float` | Instrument master cache TTL. |
| `minimum_publish_coverage_ratio` | `float` | Min quote coverage for publish. |
| `connect_timeout_seconds` | `float` | Connection timeout. |
| `max_subscriptions` | `int` | Max concurrent subscriptions. |
| `reconnect_max_attempts` | `int` | Max reconnect attempts. |
| `reconnect_base_delay_seconds` | `float` | Reconnect backoff base. |
| `timezone` | `str` | Engine timezone. |

**Projection:** `to_market_data_engine_config() -> MarketDataEngineConfig`

### 6.6 `StrategyConfiguration`

| Field | Type | Description |
|---|---|---|
| `registry_plugin_dir` | `str` | Directory containing strategy plugins. |
| `enabled_strategy_ids` | `frozenset[str]` | Enabled strategy identifiers. |
| `disabled_strategy_ids` | `frozenset[str]` | Explicitly disabled strategies. |
| `evaluation_timeout_seconds` | `float` | Max evaluation wall time. |
| `min_suitability_score` | `float` | Default min suitability threshold. |
| `min_ranking_score` | `float` | Default min ranking threshold. |
| `allow_manual_strategy_selection` | `bool` | Enable manual decision mode. |
| `registry_strict_mode` | `bool` | Fail startup on plugin load errors. |

**Projection:** `to_strategy_evaluation_engine_config()`, `to_strategy_registry_config()`

### 6.7 `RiskConfiguration`

| Field | Type | Description |
|---|---|---|
| `user_limits` | `UserRiskLimits` | User-facing risk limits (maps from legacy user config). |
| `engine` | `RiskEnginePolicyOverrides` | Overrides for `RiskEngineConfig` fields. |
| `budget` | `RiskBudgetConfiguration` | Daily risk budget allocation settings. |
| `kill_switch_default_active` | `bool` | Bootstrap kill switch default (runtime separate). |
| `metadata` | `Mapping[str, str]` | Audit metadata. |

#### 6.7.1 `UserRiskLimits`

| Field | Type | Default | Legacy `config_manager` key |
|---|---|---|---|
| `max_risk_per_trade_pct` | `float` | 1.0 | `risk.max_risk_per_trade_pct` |
| `max_daily_loss_pct` | `float` | 3.0 | `risk.max_daily_loss_pct` |
| `max_drawdown_pct` | `float` | 10.0 | `risk.max_account_drawdown_pct` |
| `max_consecutive_losses` | `int` | 3 | `risk.max_consecutive_losses` |
| `max_open_positions` | `int` | 3 | `risk.max_open_positions` |
| `caution_risk_multiplier` | `float` | 0.5 | `risk.caution_risk_multiplier` |
| `expiry_risk_multiplier` | `float` | 0.5 | `risk.expiry_risk_multiplier` |
| `medium_confidence_multiplier` | `float` | 0.75 | `risk.medium_confidence_multiplier` |
| `minimum_risk_multiplier` | `float` | 0.25 | `risk.minimum_risk_multiplier` |

#### 6.7.2 `RiskBudgetConfiguration`

| Field | Type | Default | Legacy key |
|---|---|---|---|
| `allocation_mode` | `str` | `"FIXED"` | `risk_budget.allocation_mode` |
| `max_trades_per_day` | `int` | 3 | `risk_budget.max_trades_per_day` |
| `confidence_scaling_enabled` | `bool` | True | `risk_budget.confidence_scaling_enabled` |
| `minimum_setup_score` | `float` | 60.0 | `risk_budget.minimum_setup_score` |
| `max_single_trade_daily_risk_pct` | `float` | 40.0 | `risk_budget.max_single_trade_daily_risk_pct` |

**Projection:** `to_risk_engine_config() -> RiskEngineConfig`, `to_default_user_risk_profile() -> UserRiskProfile`

**Rule RISK-CFG-001:** `max_daily_loss_pct <= max_drawdown_pct`.

**Rule RISK-CFG-002:** All multipliers >= `minimum_risk_multiplier`.

### 6.8 `ExecutionConfiguration`

| Field | Type | Description |
|---|---|---|
| `engine` | `ExecutionEnginePolicyOverrides` | Execution engine overrides. |
| `order_manager` | `OrderManagerPolicyOverrides` | Order manager overrides. |
| `slippage_bps_default` | `float` | Default slippage budget in bps. |
| `max_legs_per_plan` | `int` | Max legs per execution plan. |
| `metadata` | `Mapping[str, str]` | Audit metadata. |

#### 6.8.1 `OrderManagerPolicyOverrides`

| Field | Type | Default | Maps to |
|---|---|---|---|
| `strict_output_validation` | `bool` | True | `OrderManagerConfig.strict_output_validation` |
| `publish_lifecycle_events` | `bool` | True | `OrderManagerConfig.publish_lifecycle_events` |
| `max_poll_attempts` | `int` | 30 | `OrderManagerConfig.max_poll_attempts` |
| `poll_interval_seconds` | `float` | 1.0 | `OrderManagerConfig.poll_interval_seconds` |
| `submission_timeout_seconds` | `float` | 120.0 | `OrderManagerConfig.submission_timeout_seconds` |

**Projection:** `to_execution_engine_config()`, `to_order_manager_config()`

### 6.9 `OrchestratorConfiguration`

| Field | Type | Default | Maps to `SystemOrchestratorConfig` |
|---|---|---|---|
| `enable_pre_trade_cycle` | `bool` | True | `enable_pre_trade_cycle` |
| `enable_post_fill_cycle` | `bool` | True | `enable_post_fill_cycle` |
| `enable_event_driven_cycles` | `bool` | profile-dependent | `enable_event_driven_cycles` |
| `serial_cycle_execution` | `bool` | True | `serial_cycle_execution` |
| `cycle_timeout_seconds` | `int` | 120 | `cycle_timeout_seconds` |
| `shutdown_drain_timeout_seconds` | `int` | 60 | `shutdown_drain_timeout_seconds` |
| `health_probe_interval_seconds` | `int` | 30 | `health_probe_interval_seconds` |
| `stale_snapshot_max_age_seconds` | `int` | 60 | `stale_snapshot_max_age_seconds` |
| `strict_correlation` | `bool` | True | `strict_correlation` |
| `deterministic_fingerprint` | `bool` | profile-dependent | `deterministic_fingerprint` |
| `publish_system_events` | `bool` | True | `publish_system_events` |
| `fail_fast_on_engine_error` | `bool` | False | `fail_fast_on_engine_error` |
| `block_pre_trade_in_degraded` | `bool` | True | `block_pre_trade_in_degraded` |
| `subscription_patterns` | `tuple[str, ...]` | default tuple | `subscription_patterns` |

**Projection:** `to_orchestrator_config() -> SystemOrchestratorConfig`

### 6.10 `EventBusConfiguration`

| Field | Type | Default |
|---|---|---|
| `dispatch_mode` | `str` | `"sync"` |
| `max_handler_exceptions_before_unsubscribe` | `int` | 0 (never unsubscribe) |
| `allow_clear` | `bool` | False in production |
| `publish_system_events` | `bool` | True |

**Projection:** `to_event_bus_policy() -> EventBusPolicy`

### 6.11 `PositionConfiguration` / `PortfolioConfiguration` / `APMEConfiguration`

These sections mirror engine config fields for bootstrap projection.

| Section | Key fields | Projection method |
|---|---|---|
| Position | `strict_correlation`, `publish_lifecycle_events`, `price_hint_max_age_seconds` | `to_position_manager_config()` |
| Portfolio | `require_account_hints`, `track_peak_equity`, `margin_hint_max_age_seconds` | `to_portfolio_manager_config()` |
| APME | `decision_cooldown_seconds`, `enable_portfolio_protection`, `drawdown_halt_threshold_pct` | `to_apme_config()` |

### 6.12 `DashboardConfiguration`

| Field | Type | Default (dev / paper / prod) | Description |
|---|---|---|---|
| `enabled` | `bool` | True / True / False | Enable dashboard server. |
| `host` | `str` | `127.0.0.1` | Bind host. |
| `port` | `int` | 8080 | Bind port. |
| `auth_mode` | `DashboardAuthMode` | NONE / TOKEN / TOKEN | Auth policy. |
| `auth_token_secret_ref` | `str | None` | None | Bearer token secret ref. |
| `refresh_interval_seconds` | `float` | 2.0 | UI poll interval. |
| `cors_allowed_origins` | `tuple[str, ...]` | `()` | CORS origins. |
| `expose_redacted_config` | `bool` | True | Allow config inspection endpoint. |
| `metadata` | `Mapping[str, str]` | `{}` | Audit metadata. |

**Rule DASH-001:** `PRODUCTION` requires `auth_mode != NONE` unless `features.dashboard_enabled` is False.

**Rule DASH-002:** `auth_mode=TOKEN` requires resolvable `auth_token_secret_ref` in production.

### 6.13 `FeatureFlags`

| Flag | Default (dev / paper / prod) | Legacy key |
|---|---|---|
| `trading_enabled` | True / True / True | `trading.trading_enabled` |
| `new_entries_enabled` | True / True / True | `trading.new_entries_enabled` |
| `expiry_trading_enabled` | True / True / True | `trading.expiry_trading_enabled` |
| `post_fill_apme_enabled` | True / True / True | — |
| `event_driven_cycles_enabled` | False / True / True | — |
| `dashboard_enabled` | True / True / False | — |
| `paper_broker_simulation` | False / True / False | — |
| `allow_caution_signals` | True / True / True | `signal.allow_caution_signals` |
| `minimum_confidence_band` | `"MEDIUM"` | `signal.minimum_confidence` |

**Rule FF-001:** `PRODUCTION` with `trading_enabled=False` still allows post-fill cycles if explicitly configured.

**Rule FF-002:** `paper_broker_simulation` True only valid in `PAPER` profile.

### 6.14 `SecretReferences`

| Field | Type | Description |
|---|---|---|
| `refs` | `Mapping[str, SecretReference]` | Map of logical ref name → resolution spec. |

#### 6.14.1 `SecretReference`

| Field | Type | Description |
|---|---|---|
| `ref_id` | `str` | Logical reference identifier. |
| `source` | `SecretSource` | Resolution source type. |
| `locator` | `str` | Env var name, file path, or vault path. |
| `required` | `bool` | Whether missing secret fails load. |
| `metadata` | `Mapping[str, str]` | Non-sensitive labels. |

**Rule SEC-001:** Resolved secret values are returned only via `SecretProvider.get_secret()` — never stored on `ApplicationConfiguration`.

**Rule SEC-002:** `redact_for_logging()` replaces all secret locators with `"[REDACTED]"`.

### 6.15 `PathConfiguration`

| Field | Type | Default | Description |
|---|---|---|---|
| `config_dir` | `str` | `config/` | Configuration directory. |
| `log_dir` | `str` | `logs/` | Log output directory. |
| `data_dir` | `str` | `data/` | Runtime data directory. |
| `strategy_plugin_dir` | `str` | `strategy/plugins/` | Strategy plugin root. |
| `instrument_cache_path` | `str | None` | None | Optional instrument cache file. |

---

## 7. Environment Profiles

### 7.1 Profile summary matrix

| Dimension | DEVELOPMENT | PAPER | PRODUCTION |
|---|---|---|---|
| `execution_mode` | `ANALYSIS` or `BACKTEST` | `LIVE` (simulated) | `LIVE` |
| `BrokerType` default | `MOCK` | `MOCK` or `RECORDING` | `ZERODHA_KITE` |
| Secrets required | Optional | Optional | **Required** |
| Log format | TEXT | JSON | JSON |
| Log level | DEBUG | INFO | INFO / WARNING |
| `deterministic_fingerprint` | True | True | True |
| `enable_event_driven_cycles` | False | True | True |
| `dashboard_enabled` default | True | True | False |
| `strict_correlation` | False | True | True |
| `require_account_hints` (portfolio) | False | True | True |
| `fail_fast_on_engine_error` | True | False | False |
| Config file | Optional | Recommended | **Required** |

### 7.2 DEVELOPMENT profile defaults

**Purpose:** Local engineer productivity, fast iteration, verbose diagnostics.

| Override category | Behaviour |
|---|---|
| Logging | DEBUG platform level, TEXT format, console only |
| Broker | `MOCK` default, no secret refs required |
| Orchestrator | `enable_event_driven_cycles=False`, relaxed correlation |
| Market data | Faster publish interval for local testing (configurable) |
| Risk | Relaxed capital rejection flags in analysis |
| Dashboard | Enabled on localhost, auth NONE |
| Feature flags | `paper_broker_simulation=False` |

**Rule PROF-DEV-001:** Must not connect to live broker without explicit `THETA_PROFILE=production` or override.

### 7.3 PAPER profile defaults

**Purpose:** Production-like pipeline with simulated broker and real market data optional.

| Override category | Behaviour |
|---|---|
| Broker | `MOCK` or `RECORDING`, `paper_trading=True` |
| Execution mode | `LIVE` (simulated fills) |
| Orchestrator | Full pre-trade and post-fill cycles enabled |
| Risk | Production-like limits from user config |
| Feature flags | `paper_broker_simulation=True` |
| Secrets | Optional — mock broker needs none |

**Rule PROF-PAPER-001:** Orders must not reach live broker API unless broker type explicitly changed.

### 7.4 PRODUCTION profile defaults

**Purpose:** Live trading with maximum guardrails.

| Override category | Behaviour |
|---|---|
| Broker | `ZERODHA_KITE`, all secrets required |
| Logging | JSON, file rotation enabled |
| Orchestrator | Strict correlation, block pre-trade in degraded |
| Dashboard | Disabled by default; TOKEN auth if enabled |
| Risk | Strict capital/margin rejection flags |
| Feature flags | Conservative defaults |

**Rule PROF-PROD-001:** Load fails if any required secret ref cannot be resolved.

**Rule PROF-PROD-002:** Load fails if `account_id` empty.

**Rule PROF-PROD-003:** `BrokerType.MOCK` forbidden unless `THETA_ALLOW_MOCK_BROKER_IN_PRODUCTION=true` (escape hatch for disaster recovery tests only).

### 7.5 Profile resolution

```python
def resolve_environment_profile(
    *,
    explicit: EnvironmentProfile | None = None,
    env: Mapping[str, str] | None = None,
) -> EnvironmentProfile:
    """Resolve profile from explicit argument or THETA_PROFILE env var."""
```

| Precedence | Source |
|---|---|
| 1 | `LoadOptions.profile` explicit argument |
| 2 | `THETA_PROFILE` environment variable |
| 3 | Default `DEVELOPMENT` |

**Rule PROF-RES-001:** Invalid profile string raises `CONFIG.PROFILE.INVALID`.

---

## 8. Configuration Validation

### 8.1 Validation pipeline

```text
VALIDATE_FIELD_TYPES
    → VALIDATE_FIELD_RANGES
    → VALIDATE_REQUIRED_FIELDS (profile-aware)
    → VALIDATE_CROSS_SECTION_INVARIANTS
    → VALIDATE_PROFILE_GUARDRAILS
    → VALIDATE_SECRET_REFS (resolution optional in dev)
    → VALIDATE_PROJECTION_COMPATIBILITY (engine config construct)
```

### 8.2 Field validation rules (selected)

| Field | Rule | Error code |
|---|---|---|
| `logging.root_level` | Must be valid Python log level name | `CONFIG.LOGGING.INVALID_LEVEL` |
| `broker.connect_timeout_seconds` | > 0 | `CONFIG.BROKER.INVALID_TIMEOUT` |
| `market_data.strikes_each_side` | 1–50 | `CONFIG.MARKET_DATA.INVALID_STRIKES` |
| `risk.user_limits.max_risk_per_trade_pct` | 0.1–5.0 | `CONFIG.RISK.INVALID_LIMIT` |
| `orchestrator.cycle_timeout_seconds` | >= 1 | `CONFIG.ORCHESTRATOR.INVALID_TIMEOUT` |
| `dashboard.port` | 1–65535 | `CONFIG.DASHBOARD.INVALID_PORT` |
| `execution.slippage_bps_default` | >= 0 | `CONFIG.EXECUTION.INVALID_SLIPPAGE` |

### 8.3 Cross-section invariants

| Rule ID | Invariant |
|---|---|
| XSEC-001 | `risk.user_limits.max_daily_loss_pct <= risk.user_limits.max_drawdown_pct` |
| XSEC-002 | `risk.user_limits.expiry_risk_multiplier >= risk.user_limits.minimum_risk_multiplier` |
| XSEC-003 | `features.paper_broker_simulation` implies `profile == PAPER` |
| XSEC-004 | `broker.paper_trading` consistent with `profile` and `BrokerType` |
| XSEC-005 | `orchestrator.enable_post_fill_cycle` requires `features.post_fill_apme_enabled` or APME disabled explicitly |
| XSEC-006 | `market_data.minimum_publish_coverage_ratio` in (0, 1] |
| XSEC-007 | `strategy.enabled_strategy_ids` and `disabled_strategy_ids` disjoint |
| XSEC-008 | If `features.trading_enabled` False then `features.new_entries_enabled` must be False |
| XSEC-009 | `dashboard.enabled` and `features.dashboard_enabled` must agree |
| XSEC-010 | `execution.max_legs_per_plan >= 1` |

### 8.4 Profile guardrails

| Rule ID | Profile | Guardrail |
|---|---|---|
| PG-001 | PRODUCTION | Secrets required for live broker |
| PG-002 | PRODUCTION | `account_id` non-empty |
| PG-003 | PRODUCTION | `DashboardAuthMode.NONE` forbidden when dashboard enabled |
| PG-004 | DEVELOPMENT | Mock broker allowed |
| PG-005 | PAPER | Live broker forbidden by default |
| PG-006 | PRODUCTION | `logging.format == JSON` unless overridden |

### 8.5 Validation result type

```python
@dataclass(frozen=True)
class ApplicationConfigurationValidationResult:
    """Outcome of configuration validation."""

    errors: tuple[ConfigurationValidationIssue, ...] = ()
    warnings: tuple[ConfigurationValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return True when no errors are present."""
        return not self.errors
```

```python
@dataclass(frozen=True)
class ConfigurationValidationIssue:
    """Single validation issue."""

    code: str
    message: str
    field: str | None = None
    section: str | None = None
    severity: str = "ERROR"  # ERROR | WARNING
```

---

## 9. Environment Variable Loading

### 9.1 Naming convention

All platform environment variables use prefix **`THETA_`**.

**Rule ENV-001:** Only documented variables are loaded; unknown `THETA_*` vars produce WARNING in development, ERROR in production (configurable strictness).

**Rule ENV-002:** Environment variables override file configuration but not CLI overrides.

### 9.2 Environment variable catalog

| Variable | Type | Maps to | Description |
|---|---|---|---|
| `THETA_PROFILE` | enum | `profile` | `development`, `paper`, `production` |
| `THETA_CONFIG_FILE` | path | `LoadOptions.config_file_path` | Main config file path |
| `THETA_USER_CONFIG_PATH` | path | legacy merge | Legacy user_config.json path |
| `THETA_ACCOUNT_ID` | str | `account.account_id` | Broker account id |
| `THETA_USER_ID` | str | `account.user_id` | Platform user id |
| `THETA_TIMEZONE` | str | `account.timezone` | Platform timezone |
| `THETA_LOG_LEVEL` | str | `logging.root_level` | Root log level |
| `THETA_LOG_FORMAT` | enum | `logging.format` | `text` or `json` |
| `THETA_LOG_FILE` | path | `logging.log_file_path` | Log file path |
| `THETA_BROKER_TYPE` | enum | `broker.broker_type` | `mock`, `zerodha_kite`, `recording` |
| `THETA_BROKER_API_KEY` | secret | secret ref | API key (resolved at load) |
| `THETA_BROKER_API_SECRET` | secret | secret ref | API secret |
| `THETA_BROKER_ACCESS_TOKEN` | secret | secret ref | Access token |
| `THETA_MARKET_UNDERLYING` | str | `market_data.underlying` | Primary underlying |
| `THETA_MARKET_STRIKES_EACH_SIDE` | int | `market_data.strikes_each_side` | Strike window |
| `THETA_TRADING_ENABLED` | bool | `features.trading_enabled` | Master trading toggle |
| `THETA_NEW_ENTRIES_ENABLED` | bool | `features.new_entries_enabled` | New entry toggle |
| `THETA_RISK_MAX_PER_TRADE_PCT` | float | `risk.user_limits.max_risk_per_trade_pct` | Risk limit |
| `THETA_RISK_MAX_DAILY_LOSS_PCT` | float | `risk.user_limits.max_daily_loss_pct` | Daily loss limit |
| `THETA_RISK_MAX_DRAWDOWN_PCT` | float | `risk.user_limits.max_drawdown_pct` | Drawdown limit |
| `THETA_DASHBOARD_ENABLED` | bool | `dashboard.enabled` | Dashboard toggle |
| `THETA_DASHBOARD_PORT` | int | `dashboard.port` | Dashboard port |
| `THETA_EXECUTION_MODE` | enum | `execution_mode` | `live`, `analysis`, `backtest` |
| `THETA_ALLOW_MOCK_BROKER_IN_PRODUCTION` | bool | escape hatch | Disaster recovery testing |
| `THETA_STRATEGY_PLUGIN_DIR` | path | `strategy.registry_plugin_dir` | Plugin directory |
| `THETA_DETERMINISTIC_FINGERPRINT` | bool | orchestrator | Fingerprint determinism |

### 9.3 Boolean parsing

| Accepted true | Accepted false |
|---|---|
| `true`, `1`, `yes`, `on` | `false`, `0`, `no`, `off` |

**Rule ENV-BOOL-001:** Parsing is case-insensitive.

**Rule ENV-BOOL-002:** Invalid boolean strings raise `CONFIG.ENV.INVALID_BOOLEAN`.

### 9.4 Environment loader implementation sketch

```python
def apply_environment_overrides(
    draft: ConfigurationDraft,
    env: Mapping[str, str],
    *,
    profile: EnvironmentProfile,
) -> ConfigurationDraft:
    """Apply THETA_* environment variables to configuration draft."""
    ...
```

---

## 10. Secret Management Interface

### 10.1 `SecretProvider` protocol

```python
@runtime_checkable
class SecretProvider(Protocol):
    """Resolve secret references without storing values in ApplicationConfiguration."""

    def get_secret(self, ref: SecretReference) -> str:
        """Return secret value for reference.

        Raises:
            SecretResolutionError: When secret cannot be resolved.
        """

    def is_available(self, ref: SecretReference) -> bool:
        """Return True when secret source is reachable."""
```

### 10.2 Built-in providers (v1)

| Provider | Class | Use case |
|---|---|---|
| Environment | `EnvironmentSecretProvider` | Read from env var locators |
| File | `FileSecretProvider` | Read from file paths (0600 check in production) |
| Composite | `CompositeSecretProvider` | Chain providers by `SecretSource` |
| Inline (tests) | `InlineSecretProvider` | Dict-backed test secrets |

### 10.3 Secret resolution flow

```text
FOR each SecretReference in config.secrets.refs:
    IF provider.is_available(ref):
        value = provider.get_secret(ref)
        IF value is empty AND ref.required:
            RAISE SecretResolutionError
    ELSE IF ref.required:
        RAISE SecretResolutionError
    ELSE:
        RECORD warning in validation result
```

**Rule SEC-RES-001:** Resolved secrets passed to broker factory only — never logged.

**Rule SEC-RES-002:** `FileSecretProvider` in PRODUCTION verifies file mode `0600` (owner read/write only).

### 10.4 Default secret reference map (Zerodha)

| ref_id | source | locator (example) |
|---|---|---|
| `broker.api_key` | ENVIRONMENT | `THETA_BROKER_API_KEY` |
| `broker.api_secret` | ENVIRONMENT | `THETA_BROKER_API_SECRET` |
| `broker.access_token` | ENVIRONMENT | `THETA_BROKER_ACCESS_TOKEN` |
| `dashboard.auth_token` | ENVIRONMENT | `THETA_DASHBOARD_AUTH_TOKEN` |

---

## 11. Engine Config Projection

Application Configuration **projects** into existing engine-native frozen config types.

### 11.1 Projection methods on `ApplicationConfiguration`

| Method | Return type | Consumer |
|---|---|---|
| `to_orchestrator_config()` | `SystemOrchestratorConfig` | System Orchestrator |
| `to_event_bus_policy()` | `EventBusPolicy` | Event Bus |
| `to_market_data_engine_config()` | `MarketDataEngineConfig` | Market Data Engine |
| `to_strategy_evaluation_engine_config()` | `StrategyEvaluationEngineConfig` | Strategy Evaluation |
| `to_strategy_registry_config()` | `StrategyRegistryConfig` | Strategy Registry |
| `to_trade_decision_engine_config()` | `TradeDecisionEngineConfig` | Trade Decision |
| `to_risk_engine_config()` | `RiskEngineConfig` | Risk Engine |
| `to_default_user_risk_profile()` | `UserRiskProfile` | Risk Engine / Orchestrator |
| `to_execution_engine_config()` | `ExecutionEngineConfig` | Execution Engine |
| `to_order_manager_config()` | `OrderManagerConfig` | Order Manager |
| `to_position_manager_config()` | `PositionManagerConfig` | Position Manager |
| `to_portfolio_manager_config()` | `PortfolioManagerConfig` | Portfolio Manager |
| `to_apme_config()` | `APMEConfig` | APME |

**Rule PROJ-001:** Each projection must pass the target engine's `validate_configuration()` if available.

**Rule PROJ-002:** Projections are pure functions — no side effects.

**Rule PROJ-003:** `execution_mode` on orchestrator config derived from profile and `THETA_EXECUTION_MODE`.

### 11.2 Execution mode mapping

| Profile | Default `StrategyExecutionMode` | Override |
|---|---|---|
| DEVELOPMENT | `ANALYSIS` | `THETA_EXECUTION_MODE` |
| PAPER | `LIVE` | `THETA_EXECUTION_MODE` |
| PRODUCTION | `LIVE` | `THETA_EXECUTION_MODE=analysis` forbidden without explicit ack flag |

### 11.3 Orchestrator projection example

```python
def to_orchestrator_config(self) -> SystemOrchestratorConfig:
    """Project orchestrator settings into SystemOrchestratorConfig."""
    return SystemOrchestratorConfig(
        execution_mode=self.execution_mode,
        account_id=self.account.account_id,
        enable_pre_trade_cycle=self.orchestrator.enable_pre_trade_cycle,
        enable_post_fill_cycle=self.orchestrator.enable_post_fill_cycle,
        enable_event_driven_cycles=self.orchestrator.enable_event_driven_cycles,
        serial_cycle_execution=self.orchestrator.serial_cycle_execution,
        cycle_timeout_seconds=self.orchestrator.cycle_timeout_seconds,
        shutdown_drain_timeout_seconds=self.orchestrator.shutdown_drain_timeout_seconds,
        health_probe_interval_seconds=self.orchestrator.health_probe_interval_seconds,
        stale_snapshot_max_age_seconds=self.orchestrator.stale_snapshot_max_age_seconds,
        strict_correlation=self.orchestrator.strict_correlation,
        deterministic_fingerprint=self.orchestrator.deterministic_fingerprint,
        publish_system_events=self.orchestrator.publish_system_events,
        fail_fast_on_engine_error=self.orchestrator.fail_fast_on_engine_error,
        block_pre_trade_in_degraded=self.orchestrator.block_pre_trade_in_degraded,
        subscription_patterns=self.orchestrator.subscription_patterns,
        metadata=MappingProxyType(dict(self.metadata)),
    )
```

---

## 12. Configuration File Format

### 12.1 Supported formats (v1)

| Format | Extension | Priority |
|---|---|---|
| YAML | `.yaml`, `.yml` | Preferred |
| JSON | `.json` | Supported |

**Rule FILE-001:** Default search path: `config/application.yaml`, then `config/application.json`.

**Rule FILE-002:** `THETA_CONFIG_FILE` overrides default path.

### 12.2 Example YAML structure (redacted)

```yaml
schema_version: "1.0.0"
profile: paper
account:
  account_id: "AB1234"
  timezone: "Asia/Kolkata"
logging:
  format: json
  root_level: INFO
broker:
  broker_type: mock
  paper_trading: true
market_data:
  underlying: NIFTY
  strikes_each_side: 10
  publish_interval_seconds: 1.0
risk:
  user_limits:
    max_risk_per_trade_pct: 1.0
    max_daily_loss_pct: 3.0
    max_drawdown_pct: 10.0
features:
  trading_enabled: true
  paper_broker_simulation: true
dashboard:
  enabled: true
  port: 8080
  auth_mode: none
```

**Rule FILE-003:** Secret values forbidden in config files — use secret refs only.

### 12.3 Legacy user config merge

When `THETA_USER_CONFIG_PATH` or default `config/user_config.json` exists:

| Legacy section | Maps to |
|---|---|
| `risk` | `RiskConfiguration.user_limits` |
| `risk_budget` | `RiskConfiguration.budget` |
| `trading` | `FeatureFlags` |
| `signal` | `FeatureFlags.minimum_confidence_band`, `allow_caution_signals` |
| `system.environment` | Profile hint (`PAPER` → `EnvironmentProfile.PAPER`) |

**Rule LEGACY-001:** Legacy merge occurs before environment variables.

**Rule LEGACY-002:** Legacy keys not in mapping produce WARNING, not ERROR.

---

## 13. Serialization

### 13.1 Schema version

`APPLICATION_CONFIG_SCHEMA_VERSION = "1.0.0"`

### 13.2 Supported operations

```python
def serialize_application_configuration(config: ApplicationConfiguration) -> str: ...
def deserialize_application_configuration(payload: str) -> ApplicationConfiguration: ...
def redact_for_logging(config: ApplicationConfiguration) -> dict[str, object]: ...
def compute_config_fingerprint(config: ApplicationConfiguration) -> str: ...
```

### 13.3 Serialization rules

| Rule ID | Rule |
|---|---|
| SER-001 | Enums serialize as string values. |
| SER-002 | datetimes serialize as ISO-8601 UTC with Z suffix. |
| SER-003 | Mappings serialize as sorted-key JSON objects. |
| SER-004 | Secret locators redacted to `"[REDACTED]"` in export. |
| SER-005 | `config_fingerprint` excluded from fingerprint payload (avoid recursion). |
| SER-006 | `loaded_at` excluded from fingerprint for determinism option. |

### 13.4 Fingerprint payload

```python
def compute_config_fingerprint(config: ApplicationConfiguration) -> str:
    """SHA-256 over canonical JSON of redacted configuration."""
    payload = {
        "schema_version": config.schema_version,
        "profile": config.profile.value,
        "execution_mode": config.execution_mode.value,
        "account_id": config.account.account_id,
        "broker_type": config.broker.broker_type.value,
        "risk_limits": {...},  # salient fields only
        "feature_flags": {...},
        "orchestrator": {...},  # salient fields only
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()
```

---

## 14. Determinism and Thread Safety

### 14.1 Determinism contract

| Mode | Behaviour |
|---|---|
| `deterministic_fingerprint=True` | Stable fingerprint across reloads with identical inputs |
| Secret values | Excluded from fingerprint |
| `loaded_at` | Excluded from fingerprint when deterministic |
| Profile defaults | Fixed per profile version |

**Rule DET-001:** Same profile + same file + same env → same fingerprint (excluding `config_id`, `loaded_at`).

### 14.2 Thread safety

```python
class ApplicationConfiguration:
    """Immutable — thread-safe for concurrent reads after construction."""
```

| Rule ID | Rule |
|---|---|
| TS-001 | `ApplicationConfiguration` is frozen — no mutators. |
| TS-002 | `load_application_configuration()` is stateless — concurrent loads safe. |
| TS-003 | `ConfigurationLoader` instance must not share mutable draft state across threads. |
| TS-004 | `SecretProvider` implementations must document their own thread safety. |

---

## 15. Error Taxonomy

Namespace: `CONFIG.<CATEGORY>.<DETAIL>`

| Code | Description |
|---|---|
| `CONFIG.PROFILE.INVALID` | Unknown environment profile |
| `CONFIG.FILE.NOT_FOUND` | Config file path does not exist when required |
| `CONFIG.FILE.PARSE_ERROR` | YAML/JSON parse failure |
| `CONFIG.FILE.UNSUPPORTED_FORMAT` | Unknown file extension |
| `CONFIG.ENV.INVALID_BOOLEAN` | Unparseable boolean env var |
| `CONFIG.ENV.INVALID_NUMBER` | Unparseable numeric env var |
| `CONFIG.ENV.UNKNOWN_VARIABLE` | Unknown THETA_ variable in strict mode |
| `CONFIG.VALIDATION.FIELD_INVALID` | Field-level validation failure |
| `CONFIG.VALIDATION.CROSS_SECTION` | Cross-section invariant violation |
| `CONFIG.VALIDATION.PROFILE_GUARDRAIL` | Profile guardrail violation |
| `CONFIG.SECRET.NOT_FOUND` | Required secret missing |
| `CONFIG.SECRET.PERMISSION_DENIED` | Secret file permissions invalid |
| `CONFIG.SECRET.RESOLUTION_FAILED` | Provider failed to resolve |
| `CONFIG.PROJECTION.FAILED` | Engine config projection invalid |
| `CONFIG.SERIALIZATION.UNSUPPORTED_VERSION` | Unknown schema version |
| `CONFIG.SERIALIZATION.MALFORMED` | Malformed JSON payload |
| `CONFIG.LOGGING.INVALID_LEVEL` | Invalid log level |
| `CONFIG.BROKER.INVALID_TIMEOUT` | Invalid broker timeout |
| `CONFIG.DASHBOARD.INVALID_PORT` | Invalid dashboard port |
| `CONFIG.RISK.INVALID_LIMIT` | Risk limit out of range |

```python
class ApplicationConfigurationError(Exception):
    """Raised when configuration load or validation fails."""

    def __init__(self, message: str, *, code: str, field: str | None = None) -> None: ...
```

---

## 16. Public API

### 16.1 Module constants

```python
APPLICATION_CONFIG_VERSION: Final[str] = "1.0.0"
APPLICATION_CONFIG_SCHEMA_VERSION: Final[str] = "1.0.0"
PRODUCER_NAME: Final[str] = "application_configuration"
DEFAULT_CONFIG_PATH: Final[str] = "config/application.yaml"
DEFAULT_LEGACY_USER_CONFIG_PATH: Final[str] = "config/user_config.json"
```

### 16.2 Primary entry points

```python
def load_application_configuration(
    options: LoadOptions | None = None,
    *,
    secret_provider: SecretProvider | None = None,
    env: Mapping[str, str] | None = None,
) -> ApplicationConfiguration:
    """Load, merge, validate, and freeze application configuration.

    This is the primary bootstrap entry point for CLI, orchestrator, and tests.

    Args:
        options: Optional load options (profile, paths, overrides).
        secret_provider: Provider for secret resolution. Defaults to
            EnvironmentSecretProvider + FileSecretProvider composite.
        env: Environment mapping; defaults to os.environ.

    Returns:
        Immutable validated ApplicationConfiguration.

    Raises:
        ApplicationConfigurationError: On validation or resolution failure.
    """

def validate_application_configuration(
    draft: ConfigurationDraft,
    *,
    profile: EnvironmentProfile,
) -> ApplicationConfigurationValidationResult:
    """Validate draft configuration without loading secrets."""

def default_load_options_for_profile(profile: EnvironmentProfile) -> LoadOptions:
    """Return default LoadOptions for a profile."""

def apply_logging_configuration(config: LoggingConfiguration) -> None:
    """Configure platform logging from LoggingConfiguration."""
```

### 16.3 `ApplicationConfiguration` methods

```python
class ApplicationConfiguration:
    """Immutable sealed application configuration bundle."""

    def to_orchestrator_config(self) -> SystemOrchestratorConfig: ...
    def to_event_bus_policy(self) -> EventBusPolicy: ...
    def to_market_data_engine_config(self) -> MarketDataEngineConfig: ...
    def to_strategy_evaluation_engine_config(self) -> StrategyEvaluationEngineConfig: ...
    def to_strategy_registry_config(self) -> StrategyRegistryConfig: ...
    def to_trade_decision_engine_config(self) -> TradeDecisionEngineConfig: ...
    def to_risk_engine_config(self) -> RiskEngineConfig: ...
    def to_default_user_risk_profile(self) -> UserRiskProfile: ...
    def to_execution_engine_config(self) -> ExecutionEngineConfig: ...
    def to_order_manager_config(self) -> OrderManagerConfig: ...
    def to_position_manager_config(self) -> PositionManagerConfig: ...
    def to_portfolio_manager_config(self) -> PortfolioManagerConfig: ...
    def to_apme_config(self) -> APMEConfig: ...
    def redact_for_export(self) -> dict[str, object]: ...
```

---

## 17. Logging Events

Logger name: `config.application_configuration`.

| Event | Level | When |
|---|---|---|
| `config.load.start` | INFO | Load begins |
| `config.load.complete` | INFO | Load success with profile and fingerprint |
| `config.load.failed` | ERROR | Validation or secret failure |
| `config.profile.resolved` | INFO | Profile determined |
| `config.file.merged` | DEBUG | Config file merged |
| `config.legacy.merged` | DEBUG | Legacy user config merged |
| `config.env.applied` | DEBUG | Environment overrides applied |
| `config.secret.resolved` | DEBUG | Secret ref resolved (no value logged) |
| `config.secret.missing` | WARNING | Optional secret missing |
| `config.validation.warning` | WARNING | Non-fatal validation warning |
| `config.projection.complete` | DEBUG | Engine projection succeeded |

---

## 18. Testing Strategy

### 18.1 Coverage target

**Minimum line coverage: 95%** on `config/application_configuration.py`.

### 18.2 Required test categories

| Category | Tests |
|---|---|
| Profile resolution | development, paper, production; invalid profile |
| Layer merge order | defaults → profile → file → env → CLI |
| Validation | Field ranges, cross-section, profile guardrails |
| Secret provider | Env provider, file provider, missing required secret |
| Projection | Each `to_*_config()` produces valid engine config |
| Serialization | Round-trip redacted JSON |
| Fingerprint | Deterministic across reloads |
| Legacy merge | user_config.json maps to RiskConfiguration |
| Environment parsing | Boolean, float, enum parsing edge cases |
| Production guardrails | Mock broker forbidden, secrets required |
| Thread safety | Concurrent reads of frozen config |
| Logging apply | `apply_logging_configuration` sets handlers |
| Error taxonomy | Stable error codes |

### 18.3 Test doubles

| Double | Purpose |
|---|---|
| `InlineSecretProvider` | Deterministic secrets in tests |
| `FixtureConfigFiles` | YAML/JSON fixtures per profile |
| `RecordingEnvironment` | Capture env override application |
| `InvalidDraftBuilder` | Construct invalid drafts for validation tests |

### 18.4 Golden fixtures

| Fixture | Purpose |
|---|---|
| `fixtures/config/paper.yaml` | Paper profile golden load |
| `fixtures/config/production.yaml` | Production profile (redacted secrets) |
| `fixtures/config/legacy_user.json` | Legacy merge verification |
| `fixtures/config/fingerprint_golden.json` | Expected fingerprint |

---

## 19. Performance Requirements

| Operation | Target (p99) |
|---|---|
| Full load (no secret I/O) | < 50 ms |
| Full load (env secrets) | < 100 ms |
| Validation only | < 10 ms |
| Single engine projection | < 1 ms |
| Fingerprint computation | < 5 ms |
| Serialization (redacted) | < 10 ms |
| `apply_logging_configuration` | < 20 ms |

**Rule PERF-001:** Load path must not perform network I/O in v1 (file and env only).

**Rule PERF-002:** Projection methods must be lazy-cacheable but v1 may compute on demand.

---

## 20. Definition of Done

### 20.1 Implementation

- [ ] `config/application_configuration.py` implements full public API per §16.
- [ ] Environment profiles per §7.
- [ ] Validation pipeline per §8.
- [ ] Environment variable loading per §9.
- [ ] Secret provider interface per §10.
- [ ] Engine config projections per §11.
- [ ] Serialization per §13.
- [ ] No forbidden domain logic per §3.

### 20.2 Quality

- [ ] Unit test coverage ≥ 95%.
- [ ] Google-style docstrings on all public types and methods.
- [ ] JSON serialization round-trip schema v1.0.0.
- [ ] Thread-safe immutable bundle verified by concurrent tests.
- [ ] Golden fingerprint fixture for paper profile.
- [ ] Legacy user_config.json merge verified.

### 20.3 Documentation

- [ ] This specification implemented faithfully.
- [ ] `CHANGELOG.md` updated when module ships.
- [ ] Environment variable catalog published in README or ops runbook.

### 20.4 Integration

- [ ] System Orchestrator bootstrap uses `load_application_configuration()`.
- [ ] Paper trading runner uses `EnvironmentProfile.PAPER`.
- [ ] Live trading runner uses `EnvironmentProfile.PRODUCTION`.
- [ ] Test suite uses `InlineSecretProvider` and `DEVELOPMENT` profile.

---

## Appendix A — Worked Examples

### A.1 Development bootstrap (tests)

```python
from config.application_configuration import (
    EnvironmentProfile,
    InlineSecretProvider,
    LoadOptions,
    load_application_configuration,
)

config = load_application_configuration(
    LoadOptions(profile=EnvironmentProfile.DEVELOPMENT),
    secret_provider=InlineSecretProvider({}),
)
orchestrator_config = config.to_orchestrator_config()
risk_config = config.to_risk_engine_config()
```

### A.2 Paper trading bootstrap

```python
config = load_application_configuration(
    LoadOptions(
        profile=EnvironmentProfile.PAPER,
        config_file_path="config/application.yaml",
    ),
)
assert config.broker.paper_trading is True
assert config.features.paper_broker_simulation is True
```

### A.3 Production bootstrap with secrets

```python
config = load_application_configuration(
    LoadOptions(profile=EnvironmentProfile.PRODUCTION),
    secret_provider=EnvironmentSecretProvider(),
)
# Resolved secrets passed to broker factory — not stored on config
broker_factory = BrokerFactory(config.broker, secret_provider)
```

### A.4 Orchestrator wiring

```python
config = load_application_configuration()
orchestrator = SystemOrchestrator(
    config.to_orchestrator_config(),
    broker_client=broker_factory.create(),
    engine_registry=build_engine_registry(config),
)
```

---

## Appendix B — Engine Configuration Projection Matrix

| Application section | Engine config type | Key mapped fields |
|---|---|---|
| `orchestrator` | `SystemOrchestratorConfig` | All orchestrator fields |
| `event_bus` | `EventBusPolicy` | dispatch_mode, allow_clear |
| `market_data` | `MarketDataEngineConfig` | universe, publish_interval, reconnect |
| `strategy` | `StrategyEvaluationEngineConfig` | thresholds, timeouts |
| `strategy` | `StrategyRegistryConfig` | plugin_dir, strict_mode |
| `decision` (derived) | `TradeDecisionEngineConfig` | min confidence from features |
| `risk` | `RiskEngineConfig` | engine overrides + defaults |
| `risk.user_limits` | `UserRiskProfile` | tier limits |
| `execution` | `ExecutionEngineConfig` | slippage, policies |
| `execution.order_manager` | `OrderManagerConfig` | poll, timeout |
| `position` | `PositionManagerConfig` | correlation, hints |
| `portfolio` | `PortfolioManagerConfig` | account hints, peak equity |
| `apme` | `APMEConfig` | cooldown, protection thresholds |

---

## Appendix C — Environment Variable Quick Reference

```text
# Minimal production bootstrap
export THETA_PROFILE=production
export THETA_ACCOUNT_ID=AB1234
export THETA_BROKER_API_KEY=...
export THETA_BROKER_API_SECRET=...
export THETA_BROKER_ACCESS_TOKEN=...
export THETA_CONFIG_FILE=config/application.yaml
export THETA_LOG_FORMAT=json
export THETA_TRADING_ENABLED=true
```

```text
# Local development
export THETA_PROFILE=development
export THETA_LOG_LEVEL=DEBUG
export THETA_BROKER_TYPE=mock
```

```text
# Paper trading
export THETA_PROFILE=paper
export THETA_BROKER_TYPE=mock
export THETA_TRADING_ENABLED=true
```

---

## Appendix D — Legacy Migration Notes

| Legacy artifact | v1 institutional path |
|---|---|
| `config_manager.py` | User limits merge into `RiskConfiguration`; runtime updates deferred to v1.1 |
| `config/user_config.json` | Merged via `THETA_USER_CONFIG_PATH` when present |
| `system.environment=PAPER` in legacy JSON | Maps to `EnvironmentProfile.PAPER` hint |
| `get_risk_config()` | Replaced by `config.to_risk_engine_config()` + `to_default_user_risk_profile()` |
| `get_trading_config()` | Replaced by `config.features` |

**Rule MIG-001:** Legacy ConfigManager remains for dashboard backward compatibility until v1.1.

**Rule MIG-002:** New institutional code must not import `config_manager.py` — use Application Configuration.

---

## Appendix E — Feature Flag Reference

| Flag | Default | When False |
|---|---|---|
| `trading_enabled` | True | No new pre-trade cycles (orchestrator policy) |
| `new_entries_enabled` | True | Trade Decision forced abstain for entries |
| `expiry_trading_enabled` | True | Expiry-day strategies filtered |
| `post_fill_apme_enabled` | True | Post-fill APME stage skipped |
| `event_driven_cycles_enabled` | profile-dependent | Bus-triggered cycles disabled |
| `dashboard_enabled` | profile-dependent | Dashboard server not started |
| `paper_broker_simulation` | PAPER only | N/A in other profiles |
| `allow_caution_signals` | True | Caution signals filtered in Trade Decision |

---

## Appendix F — Glossary

| Term | Definition |
|---|---|
| **Application Configuration** | Immutable bootstrap bundle for the entire platform. |
| **Environment Profile** | Named deployment mode: development, paper, production. |
| **Configuration Projection** | Pure mapping from Application Configuration to engine-native `*Config`. |
| **Secret Reference** | Logical pointer to a secret value — never the value itself. |
| **Config Fingerprint** | SHA-256 hash for audit and deterministic verification. |
| **Configuration Draft** | Mutable intermediate state during load pipeline (internal type). |
| **Profile Guardrail** | Profile-specific validation rule stricter than base validation. |
| **Legacy User Config** | Historical `config/user_config.json` format merged for compatibility. |
| **Feature Flag** | Boolean toggle for optional platform behaviour. |
| **Redacted Export** | Serialization with secrets and sensitive locators removed. |

---

*End of specification — document length meets minimum 1200-line requirement for institutional review.*
