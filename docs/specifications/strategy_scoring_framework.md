# Strategy Scoring Framework — Software Engineering Specification

| Field | Value |
|---|---|
| Module | `strategy/strategy_scoring_framework.py` |
| Document version | 1.0.0 |
| Status | Implementation contract |
| Owner | THETA AI TRADER Core Platform |
| Last updated | 2026-08-05 |
| Score domain | Finite `float` values in `[0.0, 100.0]`, sealed to four decimals |

---

## 1. Purpose

`strategy/strategy_scoring_framework.py` is the universal, deterministic scoring boundary for THETA AI TRADER v1.0. It turns already-computed categorical and numeric factor inputs into an immutable `StrategyScore`, `ConfidenceReport`, and `StrategyExplanation`.

It answers: given factor inputs supplied by a strategy plugin or orchestrator-assembled context, how are they normalized, weighted, aggregated, validated, explained, serialized, and sealed without fetching market data, calculating indicators, placing orders, or deciding a trade?

### 1.1 Gap filled

| Component | Contractual boundary |
|---|---|
| `strategy/base_strategy.py` and plugins | Implement strategy logic, produce factor inputs, and may call this framework to seal a score. They do not own universal scoring mathematics. |
| `strategy/strategy_evaluation_engine.py` | Evaluates plugin reports and ranks them. It may consume sealed scores or request suitability enrichment; it never redefines the factor taxonomy. |
| `engines/strategy_engine.py` | Aggregates signals and resolves conflicts. It may consume sealed scores. ConfidenceScorer concepts migrate toward this shared framework without replacing the engine. |
| Trade Decision Engine | Consumes ranked evaluation reports containing or referencing a score for objective comparison. It retains trade approval responsibility. |
| This module | Sole owner of factor categories, normalization, profiles, aggregation, confidence math, explanations, score validation, serialization, and scoring statistics. |

### 1.2 Frozen pipeline

```text
MarketSnapshot → Strategy plugins (BaseStrategy) → TradingSignal (+ optional StrategyScore via this framework)
→ Strategy Evaluation Engine → StrategyEvaluationBundle → Trade Decision Engine → Risk → Execution
```

The framework is not on the market-data path. A plugin may use a `MarketSnapshot` before calling this framework, but the framework receives only extracted factor values. It neither parses snapshots nor invokes external services.

### 1.3 Architecture freeze rules

- **BOUNDARY-SSF-001:** Never fetch, parse, subscribe to, cache, or infer market data.
- **BOUNDARY-SSF-002:** Never calculate EMA, RSI, VWAP, ATR, implied volatility, Greeks, open interest, or any indicator.
- **BOUNDARY-SSF-003:** Never place, modify, cancel, simulate, or route an order.
- **BOUNDARY-SSF-004:** Never call a broker API, broker adapter, HTTP client, websocket, or database.
- **BOUNDARY-SSF-005:** Never select a strategy, resolve signal conflicts, approve a trade, or enforce a risk limit.
- **BOUNDARY-SSF-006:** Never mutate a caller-owned input, emitted model, profile, or configuration.
- **BOUNDARY-SSF-007:** Never use wall-clock time to change a score; a clock is used only for injected audit timestamps.
- **BOUNDARY-SSF-008:** Never accept non-finite numeric values, implicit defaults for required factors, or unversioned payloads.
- **BOUNDARY-SSF-009:** Never replace `StrategyEvaluationEngine`, `StrategyEngine`, Risk, Execution, or a strategy plugin.
- **BOUNDARY-SSF-010:** Never permit a weight profile to silently change a sealed score.

### 1.4 Goals

1. Provide exactly one universal factor taxonomy for comparable strategies.
2. Normalize heterogeneous supplied inputs into an auditable score domain.
3. Aggregate with explicit, immutable, named weight profiles.
4. Calculate confidence from coverage, quality, agreement, and score stability.
5. Generate machine-readable and human-readable explanations.
6. Fail closed for invalid required factor inputs.
7. Be deterministic across machines, runs, and thread schedules.
8. Support versioned JSON serialization and backward-compatible readers.
9. Expose read-only statistics and optional health signals.
10. Remain simple to fake and fully test without market data or a broker.

### 1.5 Success criteria

- Equivalent validated inputs and config produce byte-equivalent canonical JSON.
- Every factor has category, raw value, normalized value, effective weight, contribution, provenance, and validation state.
- Every score exposes a confidence band and an explanation that accounts for every included or excluded factor.
- The weighted aggregate is in `[0.0, 100.0]` and is rounded only at defined sealing boundaries.
- Missing required categories, unknown profile names, non-finite values, and invalid ranges fail before a score is sealed.
- Unit coverage for `strategy/strategy_scoring_framework.py` is at least 95 percent.

---

## 2. Responsibilities

| ID | Requirement |
|---|---|
| R1 | Own the seven normative factor categories. |
| R2 | Validate factor identity, type, provenance, and raw value. |
| R3 | Normalize factor values to the canonical score domain. |
| R4 | Apply category-specific normalization rules. |
| R5 | Select only an explicitly requested or configured immutable weight profile. |
| R6 | Calculate effective weights after permitted missing-factor redistribution. |
| R7 | Aggregate normalized factors with deterministic arithmetic. |
| R8 | Apply explicit penalties for event risk and data-quality degradation. |
| R9 | Calculate coverage, agreement, dispersion, and confidence. |
| R10 | Assign LOW, MEDIUM, HIGH, or VERY_HIGH confidence bands. |
| R11 | Generate a stable ordered explanation. |
| R12 | Produce immutable frozen dataclass outputs. |
| R13 | Serialize and deserialize versioned JSON payloads. |
| R14 | Validate deserialized payload integrity. |
| R15 | Expose read-only scoring statistics. |
| R16 | Expose optional health based on recent validation outcomes. |
| R17 | Accept injected fixed clock for audit metadata. |
| R18 | Permit an optional factor-provider protocol without calling it implicitly. |
| R19 | Support optional observational event publication through an injected sink. |
| R20 | Preserve input provenance rather than deriving facts. |
| R21 | Provide compatibility adapters for evaluation-engine suitability inputs. |
| R22 | Support deterministic ranking keys without ranking strategies itself. |
| R23 | Reject unknown enum values and profile revisions. |
| R24 | Keep bounded cache access behind a lock when cache mode is enabled. |
| R25 | Document all public API contracts and error codes. |

---

## 3. Non-responsibilities

| ID | Explicit exclusion |
|---|---|
| NR1 | Market data retrieval or MarketSnapshot parsing |
| NR2 | Indicator, volatility-surface, or Greeks calculation |
| NR3 | Regime detection or trend classification |
| NR4 | Liquidity measurement or option-chain analysis |
| NR5 | Strategy implementation or signal generation |
| NR6 | Strategy selection or conflict resolution |
| NR7 | Evaluation report ownership or ranking policy |
| NR8 | Trade decision approval |
| NR9 | Risk-limit, margin, position-size, or exposure enforcement |
| NR10 | Portfolio construction or allocation |
| NR11 | Order construction, routing, brokerage, or execution |
| NR12 | Authentication, credential storage, or configuration-file loading |
| NR13 | Persistence outside an injected audit/event adapter |
| NR14 | Mutation of strategy plugin state |
| NR15 | Live-time scheduling, polling, or retry loops |
| NR16 | Predictive modelling or model inference |
| NR17 | Backtesting orchestration |
| NR18 | Market-event calendar acquisition |
| NR19 | Guaranteeing expected return or trade profitability |
| NR20 | Replacing any frozen pipeline component |

---

## 4. Scoring category catalog

All normalized factor scores are finite floats in `[0.0, 100.0]`. A higher score always means stronger suitability. Raw values are supplied facts; this module does not verify their market truth.

| Enum | Display | Accepted inputs | Raw range | Normalization | Default weight | Requirement | Typical provenance |
|---|---|---|---|---|---|---|---|
| MARKET_REGIME | Market Regime | Categorical regime label and optional plugin suitability score | Labels plus 0–100 | Profile mapping or supplied bounded suitability | 15.0000 | Required | Regime detector or plugin |
| TREND_ALIGNMENT | Trend Alignment | Directional alignment and magnitude already computed by plugin | -1–1, 0–1, or 0–100 | Signed mapping / unit scaling | 15.0000 | Required | Plugin indicator layer |
| VOLATILITY | Volatility | IV percentile, realized/expected relation, or strategy fit hint | 0–100 or 0–1 | Unit scaling / profile curve | 15.0000 | Required | Volatility analysis |
| LIQUIDITY | Liquidity | Spread, depth, volume, OI, or composite liquidity hint | 0–100 or 0–1 | Unit scaling / inverse spread curve | 15.0000 | Required | Liquidity analysis |
| GREEKS | Greeks | Delta/theta/vega/gamma exposure suitability hint | 0–100 or 0–1 | Unit scaling / safe-range curve | 10.0000 | Required | Greeks engine |
| RISK_REWARD | Risk Reward | Defined-risk, credit, loss, breakeven, or composite hint | 0–100 or ratio | Unit scaling / ratio curve | 20.0000 | Required | Plugin payoff analysis |
| EVENT_RISK | Event Risk | Known event flags, proximity, and policy suitability | boolean, 0–1, or 0–100 | Penalty-aware mapping | 10.0000 | Required | Event-risk service |

### 4.1 Market Regime

`MARKET_REGIME` is a normative category. A strategy may provide multiple factors in this category, but category contribution is bounded by its effective weight. The default profile starts at `15.0000` percent.

| Input form | Rule | Example |
|---|---|---|
| `score_0_100` | Must be finite and inclusive of 0 and 100; pass through after rounding. | `72.5` becomes `72.5000`. |
| `unit_interval` | Must be finite in `[0, 1]`; multiply by 100. | `0.725` becomes `72.5000`. |
| `signed_unit` | Only valid for trend alignment; map `(value + 1) * 50`. | `-0.20` becomes `40.0000`. |
| `label` | Only valid where a profile defines the label map; unknown labels fail closed. | `RANGE_BOUND` maps by profile. |
| `ratio` | Only valid for documented ratio metrics; use the configured monotonic curve. | `2.0` risk/reward maps by the profile curve. |

**Category rule:** provided `Market Regime` evidence is suitability evidence, not a trade authorization. It cannot override Risk or the Trade Decision Engine.

### 4.2 Trend Alignment

`TREND_ALIGNMENT` is a normative category. A strategy may provide multiple factors in this category, but category contribution is bounded by its effective weight. The default profile starts at `15.0000` percent.

| Input form | Rule | Example |
|---|---|---|
| `score_0_100` | Must be finite and inclusive of 0 and 100; pass through after rounding. | `72.5` becomes `72.5000`. |
| `unit_interval` | Must be finite in `[0, 1]`; multiply by 100. | `0.725` becomes `72.5000`. |
| `signed_unit` | Only valid for trend alignment; map `(value + 1) * 50`. | `-0.20` becomes `40.0000`. |
| `label` | Only valid where a profile defines the label map; unknown labels fail closed. | `RANGE_BOUND` maps by profile. |
| `ratio` | Only valid for documented ratio metrics; use the configured monotonic curve. | `2.0` risk/reward maps by the profile curve. |

**Category rule:** provided `Trend Alignment` evidence is suitability evidence, not a trade authorization. It cannot override Risk or the Trade Decision Engine.

### 4.3 Volatility

`VOLATILITY` is a normative category. A strategy may provide multiple factors in this category, but category contribution is bounded by its effective weight. The default profile starts at `15.0000` percent.

| Input form | Rule | Example |
|---|---|---|
| `score_0_100` | Must be finite and inclusive of 0 and 100; pass through after rounding. | `72.5` becomes `72.5000`. |
| `unit_interval` | Must be finite in `[0, 1]`; multiply by 100. | `0.725` becomes `72.5000`. |
| `signed_unit` | Only valid for trend alignment; map `(value + 1) * 50`. | `-0.20` becomes `40.0000`. |
| `label` | Only valid where a profile defines the label map; unknown labels fail closed. | `RANGE_BOUND` maps by profile. |
| `ratio` | Only valid for documented ratio metrics; use the configured monotonic curve. | `2.0` risk/reward maps by the profile curve. |

**Category rule:** provided `Volatility` evidence is suitability evidence, not a trade authorization. It cannot override Risk or the Trade Decision Engine.

### 4.4 Liquidity

`LIQUIDITY` is a normative category. A strategy may provide multiple factors in this category, but category contribution is bounded by its effective weight. The default profile starts at `15.0000` percent.

| Input form | Rule | Example |
|---|---|---|
| `score_0_100` | Must be finite and inclusive of 0 and 100; pass through after rounding. | `72.5` becomes `72.5000`. |
| `unit_interval` | Must be finite in `[0, 1]`; multiply by 100. | `0.725` becomes `72.5000`. |
| `signed_unit` | Only valid for trend alignment; map `(value + 1) * 50`. | `-0.20` becomes `40.0000`. |
| `label` | Only valid where a profile defines the label map; unknown labels fail closed. | `RANGE_BOUND` maps by profile. |
| `ratio` | Only valid for documented ratio metrics; use the configured monotonic curve. | `2.0` risk/reward maps by the profile curve. |

**Category rule:** provided `Liquidity` evidence is suitability evidence, not a trade authorization. It cannot override Risk or the Trade Decision Engine.

### 4.5 Greeks

`GREEKS` is a normative category. A strategy may provide multiple factors in this category, but category contribution is bounded by its effective weight. The default profile starts at `10.0000` percent.

| Input form | Rule | Example |
|---|---|---|
| `score_0_100` | Must be finite and inclusive of 0 and 100; pass through after rounding. | `72.5` becomes `72.5000`. |
| `unit_interval` | Must be finite in `[0, 1]`; multiply by 100. | `0.725` becomes `72.5000`. |
| `signed_unit` | Only valid for trend alignment; map `(value + 1) * 50`. | `-0.20` becomes `40.0000`. |
| `label` | Only valid where a profile defines the label map; unknown labels fail closed. | `RANGE_BOUND` maps by profile. |
| `ratio` | Only valid for documented ratio metrics; use the configured monotonic curve. | `2.0` risk/reward maps by the profile curve. |

**Category rule:** provided `Greeks` evidence is suitability evidence, not a trade authorization. It cannot override Risk or the Trade Decision Engine.

### 4.6 Risk Reward

`RISK_REWARD` is a normative category. A strategy may provide multiple factors in this category, but category contribution is bounded by its effective weight. The default profile starts at `20.0000` percent.

| Input form | Rule | Example |
|---|---|---|
| `score_0_100` | Must be finite and inclusive of 0 and 100; pass through after rounding. | `72.5` becomes `72.5000`. |
| `unit_interval` | Must be finite in `[0, 1]`; multiply by 100. | `0.725` becomes `72.5000`. |
| `signed_unit` | Only valid for trend alignment; map `(value + 1) * 50`. | `-0.20` becomes `40.0000`. |
| `label` | Only valid where a profile defines the label map; unknown labels fail closed. | `RANGE_BOUND` maps by profile. |
| `ratio` | Only valid for documented ratio metrics; use the configured monotonic curve. | `2.0` risk/reward maps by the profile curve. |

**Category rule:** provided `Risk Reward` evidence is suitability evidence, not a trade authorization. It cannot override Risk or the Trade Decision Engine.

### 4.7 Event Risk

`EVENT_RISK` is a normative category. A strategy may provide multiple factors in this category, but category contribution is bounded by its effective weight. The default profile starts at `10.0000` percent.

| Input form | Rule | Example |
|---|---|---|
| `score_0_100` | Must be finite and inclusive of 0 and 100; pass through after rounding. | `72.5` becomes `72.5000`. |
| `unit_interval` | Must be finite in `[0, 1]`; multiply by 100. | `0.725` becomes `72.5000`. |
| `signed_unit` | Only valid for trend alignment; map `(value + 1) * 50`. | `-0.20` becomes `40.0000`. |
| `label` | Only valid where a profile defines the label map; unknown labels fail closed. | `RANGE_BOUND` maps by profile. |
| `ratio` | Only valid for documented ratio metrics; use the configured monotonic curve. | `2.0` risk/reward maps by the profile curve. |

**Category rule:** provided `Event Risk` evidence is suitability evidence, not a trade authorization. It cannot override Risk or the Trade Decision Engine.

---

## 5. Architecture

### 5.1 Component diagram

```text
BaseStrategy plugin ── extracted FactorInputBundle ──┐
Strategy Evaluation Engine ─ suitability factors ───┼──> StrategyScoringFramework
                                                         │      ├─ Validator
                                                         │      ├─ Normalizer
                                                         │      ├─ Aggregator
                                                         │      ├─ Confidence calculator
                                                         │      └─ Explanation generator
                                                         └──> StrategyScore + ConfidenceReport + StrategyExplanation
                                                                    │
                                     StrategyEvaluationBundle <─────┘
                                                                    │
                                                        Trade Decision Engine
```

### 5.2 Dependency direction

| Layer | May depend on | Must not depend on |
|---|---|---|
| Framework | stdlib, immutable config, optional injected protocols | brokers, market data, indicators, strategy implementations, decision engine |
| Plugin | framework public API | framework internals or mutable scorer state |
| Evaluation engine | sealed score/public serializers | framework private normalization functions |
| Decision engine | evaluation bundle/score projection | raw factor normalization policy |

**BOUNDARY-SSF-011:** dependencies point inward to pure models. No import from `broker`, market-data, execution, risk enforcement, or concrete strategy packages is permitted.

---

## 6. Configuration

`ScoringFrameworkConfig` is a frozen dataclass supplied by composition root. The module never reads environment variables or files.

| Field | Type | Default | Validation | Meaning |
|---|---|---|---|---|
| `schema_version` | `str` | `1.0` | exact supported version | Wire and model contract version |
| `default_profile` | `str` | `BALANCED` | known profile | Profile used when caller omits one |
| `rounding_decimals` | `int` | `4` | 0–8 | Seal precision |
| `minimum_category_coverage` | `float` | `0.80` | (0, 1] | Required coverage for a valid score |
| `allow_optional_factor_omission` | `bool` | `True` | boolean | Controls optional-factor handling |
| `enable_statistics` | `bool` | `True` | boolean | Enables lock-protected counters |
| `enable_cache` | `bool` | `False` | boolean | Permits bounded immutable result cache |
| `cache_capacity` | `int` | `256` | 0–4096 | Maximum cache entries |
| `event_sink` | `ScoringEventSink | None` | `None` | protocol | Observational publication only |

### 6.1 Weight profiles

| Profile | Regime/Trend/Vol/Liq/Greeks/RR/Event | Intended use |
|---|---|---|
| BALANCED | 15/15/15/15/10/20/10 | General comparable suitability |
| PREMIUM_SELLING | 15/10/20/20/10/15/10 | Volatility and liquidity-aware premium strategies |
| DEFINED_RISK | 15/15/15/10/10/25/10 | Payoff/risk-reward emphasis |
| DIRECTIONAL | 10/25/10/15/10/20/10 | Trend-aligned directional strategies |
| EVENT_CAUTION | 10/10/15/10/10/15/30 | Strong event-risk sensitivity |

Profile weights must sum to exactly `100.0000` before any omission redistribution. Event-risk is a suitability category; a profile may map adverse event evidence to a low score, but the framework does not impose a risk limit.

- **CFG-SSF-001:** Configuration construction validates every profile name and total.
- **CFG-SSF-002:** Profile revisions are immutable identifiers; changing a mapping requires a new revision.
- **CFG-SSF-003:** A caller may select a profile only by exact name.
- **CFG-SSF-004:** An unknown profile produces `SSF.CFG.003`.
- **CFG-SSF-005:** Custom profiles require all seven category weights and explicit label maps.
- **CFG-SSF-006:** Weight changes are audit-significant and must be recorded in the sealed score.
- **CFG-SSF-007:** The framework never mutates a shared profile dictionary.
- **CFG-SSF-008:** Profile ordering is canonical FactorCategory enum order.

---

## 7. Public API

### 7.1 Constants and enums

| Name | Values / contract |
|---|---|
| `SCORE_MIN` / `SCORE_MAX` | `0.0` / `100.0` |
| `DEFAULT_ROUNDING_DECIMALS` | `4` |
| `FactorCategory` | `MARKET_REGIME`, `TREND_ALIGNMENT`, `VOLATILITY`, `LIQUIDITY`, `GREEKS`, `RISK_REWARD`, `EVENT_RISK` |
| `RawValueKind` | `SCORE_0_100`, `UNIT_INTERVAL`, `SIGNED_UNIT`, `LABEL`, `RATIO`, `BOOLEAN` |
| `ValidationSeverity` | `ERROR`, `WARNING`, `INFO` |
| `ConfidenceBand` | `LOW`, `MEDIUM`, `HIGH`, `VERY_HIGH` |
| `ScoringHealth` | `HEALTHY`, `DEGRADED`, `UNHEALTHY`, `DISABLED` |

### 7.2 Frozen model field contracts

#### `FactorScore`

| Field | Type | Contract |
|---|---|---|
| category | FactorCategory | Normative taxonomy category |
| factor_id | str | Stable plugin-local identifier |
| raw_value | str | float | bool | Auditable supplied value |
| raw_value_kind | RawValueKind | Normalization selector |
| normalized_score | float | Sealed score in 0–100 |
| weight | float | Effective category allocation |
| contribution | float | Normalized score × effective weight / 100 |
| provenance | str | Source identity |
| validation_notes | tuple[str, ...] | Non-error validation annotations |

#### `ConfidenceReport`

| Field | Type | Contract |
|---|---|---|
| score | float | Confidence in 0–100 |
| band | ConfidenceBand | Band from documented thresholds |
| coverage | float | Weighted usable-factor coverage 0–100 |
| agreement | float | Cross-factor agreement 0–100 |
| data_quality | float | Validated provenance/precision quality 0–100 |
| penalties | tuple[str, ...] | Stable penalty codes |
| reasons | tuple[str, ...] | Ordered human reasons |

#### `StrategyExplanation`

| Field | Type | Contract |
|---|---|---|
| summary | str | Stable one-sentence explanation |
| strengths | tuple[str, ...] | Ordered positive evidence |
| concerns | tuple[str, ...] | Ordered adverse evidence |
| factor_narratives | tuple[str, ...] | One narrative per factor |
| methodology_version | str | Explanation template version |

#### `StrategyScore`

| Field | Type | Contract |
|---|---|---|
| strategy_id | str | Caller-supplied stable strategy identity |
| profile_name | str | Applied immutable profile |
| profile_revision | str | Applied profile revision |
| overall_score | float | Aggregate suitability 0–100 |
| factor_scores | tuple[FactorScore, ...] | Canonical ordered factors |
| confidence | ConfidenceReport | Confidence artifact |
| explanation | StrategyExplanation | Explanation artifact |
| schema_version | str | Wire schema |
| sealed_at | datetime | Injected UTC timestamp |
| input_fingerprint | str | Canonical SHA-256 of non-derived input |

#### `ValidationResult`

| Field | Type | Contract |
|---|---|---|
| is_valid | bool | True only with no errors |
| errors | tuple[ValidationIssue, ...] | Stable ordered errors |
| warnings | tuple[ValidationIssue, ...] | Stable ordered warnings |
| normalized_inputs | tuple[NormalizedFactorInput, ...] | Validated intermediate values |

#### `ScoringStatistics`

| Field | Type | Contract |
|---|---|---|
| requests | int | Score requests |
| sealed | int | Successful seals |
| rejected | int | Validation rejections |
| cache_hits | int | Cache retrievals |
| band_counts | Mapping[str, int] | Confidence-band totals |
| category_omissions | Mapping[str, int] | Allowed omissions |

All public models are `@dataclass(frozen=True, slots=True)`. Any mapping field is wrapped in `MappingProxyType`; sequence fields are tuples. Equality is value equality and contains no mutable references.

### 7.3 Protocols and facade

```python
class FactorProvider(Protocol):
    """Supplies already-extracted factor inputs; never called implicitly."""

    def provide(self, strategy_id: str) -> "FactorInputBundle": ...

class StrategyScorer(Protocol):
    """Seals a validated universal strategy score."""

    def score(self, request: "ScoreRequest") -> StrategyScore: ...

class StrategyScoringFramework:
    """Stateless scoring facade with optional protected observability state."""

    def validate(self, request: "ScoreRequest") -> ValidationResult: ...
    def score(self, request: "ScoreRequest") -> StrategyScore: ...
    def explain(self, score: StrategyScore) -> StrategyExplanation: ...
    def serialize(self, score: StrategyScore) -> str: ...
    def deserialize(self, payload: str) -> StrategyScore: ...
    def statistics(self) -> ScoringStatistics: ...
```

`FactorProvider` exists for integration convenience only. `StrategyScoringFramework.score()` takes a concrete request and never discovers, fetches, or calls providers.

---

## 8. Normalization pipeline

Normalization is pure, ordered, and performed before aggregation. It has no side effects except optional statistics after a terminal outcome.

- **NORM-SSF-001:** Validate request identity and profile selection.
- **NORM-SSF-002:** Canonicalize factor ordering by category then `factor_id`.
- **NORM-SSF-003:** Validate raw kind is permitted for the category.
- **NORM-SSF-004:** Reject `NaN`, positive infinity, negative infinity, and booleans masquerading as numbers.
- **NORM-SSF-005:** Apply declared unit conversion, not heuristic auto-detection.
- **NORM-SSF-006:** Apply profile label map or metric curve.
- **NORM-SSF-007:** Clamp only where the declared curve permits clamping; otherwise reject out-of-range input.
- **NORM-SSF-008:** Round normalized result with decimal half-even policy to configured precision.
- **NORM-SSF-009:** Record conversion method and warning notes.
- **NORM-SSF-010:** Reject any post-normalization result outside 0–100.
- **NORM-SSF-011:** Return immutable intermediate values.
- **NORM-SSF-012:** Do not calculate a score when validation contains an error.

### 8.1 Canonical mappings

| Kind | Domain | Formula / mapping | Invalid input |
|---|---|---|---|
| `SCORE_0_100` | [0, 100] | `round(raw, d)` | outside domain |
| `UNIT_INTERVAL` | [0, 1] | `round(raw * 100, d)` | outside domain |
| `SIGNED_UNIT` | [-1, 1] | `round((raw + 1) * 50, d)` | not Trend Alignment or outside domain |
| `BOOLEAN` | bool | profile map: safe=True→100, adverse=True→0 | unsupported category |
| `LABEL` | profile-declared labels | exact normalized label lookup | unknown or ambiguous label |
| `RATIO` | [0, ∞) | declared monotonic piecewise-linear curve | negative or absent curve |

### 8.2 Ratio curve contract

A ratio curve is `(x0, y0) ... (xn, yn)` with strictly increasing `x`, `y` in `[0,100]`, and linear interpolation. Inputs below `x0` use `y0`; inputs above `xn` use `yn` only when `allow_endpoint_clamp=True`, otherwise they are rejected. Curves are config, not plugin code.

---

## 9. Weighted aggregation

The framework aggregates suitability evidence, not an expected return. Category-level scores prevent a plugin from overpowering a category by submitting many factors.

- **AGG-SSF-001:** A category score is the weighted mean of its valid factors using per-factor reliability weights supplied in the request.
- **AGG-SSF-002:** A category without a required valid factor is an error unless explicitly optional in the selected profile.
- **AGG-SSF-003:** Optional omitted category weight is redistributed proportionally only among valid non-event categories if enabled.
- **AGG-SSF-004:** No category may receive more than its configured weight plus redistributed optional weight.
- **AGG-SSF-005:** Effective profile weights sum to exactly 100 after rounding reconciliation.
- **AGG-SSF-006:** Overall score is sum(category_score × effective_weight / 100).
- **AGG-SSF-007:** Event Risk participates as its normal category score and is never double-counted.
- **AGG-SSF-008:** A declared profile penalty may subtract at most its documented capped amount after aggregation.
- **AGG-SSF-009:** The final aggregate is clamped only for floating point epsilon, then sealed by half-even rounding.
- **AGG-SSF-010:** A sealed score includes profile name, revision, effective weights, and factor contributions.

### 9.1 Formula

```text
category_score[c] = Σ(normalized_factor[i] × reliability[i]) / Σ(reliability[i])
effective_weight[c] = configured_weight[c] + permitted_redistribution[c]
overall = round(Σ(category_score[c] × effective_weight[c] / 100) - capped_penalties, 4)
```

Reliability weights are in `(0, 1]`, are validation metadata rather than universal category weights, and are never inferred. If omitted, they default to `1.0` only when configuration permits default reliability.

---

## 10. Confidence calculation

Confidence measures confidence in the supplied scoring evidence, not trade success probability, expected profit, broker fill probability, or risk approval.

- **CONF-SSF-001:** Coverage is the effective profile weight represented by valid factors.
- **CONF-SSF-002:** Data quality starts at 100 and deducts documented penalties for stale, imprecise, degraded, or warning-marked provenance supplied by caller.
- **CONF-SSF-003:** Agreement is `100 - weighted dispersion`, where dispersion is normalized weighted mean absolute deviation from overall score.
- **CONF-SSF-004:** Confidence is `0.45 × coverage + 0.30 × agreement + 0.25 × data_quality - penalties`.
- **CONF-SSF-005:** All component values and result are bounded in 0–100 and rounded at seal time.
- **CONF-SSF-006:** A validation error has no confidence report because no score is sealed.
- **CONF-SSF-007:** Warnings can lower confidence but cannot silently change normalized factor scores.
- **CONF-SSF-008:** A profile may require an event-risk factor; absence is a coverage failure, not an assumed-safe event state.

### 10.1 Confidence bands

| Band | Inclusive lower bound | Exclusive upper bound | Meaning |
|---|---|---|---|
| LOW | 0.0000 | 45.0000 | Evidence is incomplete, conflicting, or low quality. |
| MEDIUM | 45.0000 | 70.0000 | Evidence is usable but has material limitations. |
| HIGH | 70.0000 | 85.0000 | Evidence is broad, coherent, and good quality. |
| VERY_HIGH | 85.0000 | 100.0001 | Evidence is complete, coherent, and high quality. |

A confidence band does not permit bypassing risk checks. The Trade Decision Engine owns any action threshold.

---

## 11. Explanation generation

Explanations are generated from sealed facts, not from generative inference. Stable template selection makes them reproducible and testable.

- **EXPL-SSF-001:** Order narratives by FactorCategory enum order and then factor ID.
- **EXPL-SSF-002:** Describe raw kind, normalized score, effective category weight, and contribution.
- **EXPL-SSF-003:** List strengths for normalized scores at or above 70.
- **EXPL-SSF-004:** List concerns for normalized scores below 45 or warning-marked provenance.
- **EXPL-SSF-005:** Include omissions and redistribution in the summary.
- **EXPL-SSF-006:** Include confidence components and penalty codes.
- **EXPL-SSF-007:** Never claim profitability, safety, approval, or a recommended order.
- **EXPL-SSF-008:** Never include secrets, credentials, raw market payloads, or broker identifiers.
- **EXPL-SSF-009:** Use stable numeric formatting to configured precision.
- **EXPL-SSF-010:** Use `methodology_version` to permit template evolution without rewriting history.

Template summary: `Strategy {strategy_id} scored {overall}/100 under {profile}; confidence is {band} ({confidence}/100) because coverage={coverage}, agreement={agreement}, data_quality={quality}.`

---

## 12. Validation

Validation returns a `ValidationResult`; `score()` raises `StrategyScoringValidationError` containing that result when invalid. Validation is side-effect-free.

- **VAL-SSF-001:** Request strategy ID is non-empty, normalized Unicode text of at most 128 characters.
- **VAL-SSF-002:** Factor IDs are non-empty, at most 128 characters, and unique within category.
- **VAL-SSF-003:** At least one factor is supplied.
- **VAL-SSF-004:** Every required category has at least one valid factor.
- **VAL-SSF-005:** Categories are only members of FactorCategory.
- **VAL-SSF-006:** Raw values match the declared RawValueKind exactly.
- **VAL-SSF-007:** Numeric values are finite real numbers but not bool.
- **VAL-SSF-008:** Provenance is non-empty and at most 256 characters.
- **VAL-SSF-009:** Reliability is finite in `(0, 1]`.
- **VAL-SSF-010:** Optional timestamp is UTC-aware and not used to modify score mathematics.
- **VAL-SSF-011:** Profile name and revision exist and match config.
- **VAL-SSF-012:** A requested rounding precision cannot override configuration.
- **VAL-SSF-013:** Input metadata size is bounded to prevent abuse.
- **VAL-SSF-014:** Deserialized schema version is supported.
- **VAL-SSF-015:** Canonical fingerprint matches reconstructed input when verification is requested.

---

## 13. Determinism and thread safety

The primary scoring path is stateless and re-entrant. It uses no random number generator, network, current market state, wall-clock-dependent branch, locale-dependent formatting, or unordered iteration.

| Concern | Requirement |
|---|---|
| Floating point | Use `Decimal(str(value))` for normalization and aggregation; emit float only at frozen model boundary. |
| Rounding | Use `ROUND_HALF_EVEN` with config precision at documented seal boundaries. |
| Ordering | Sort categories by enum declaration and factors by `(category.value, factor_id)`. |
| Time | Accept injected UTC clock; fixed clock is mandatory in tests. |
| Cache | Disabled by default. If enabled, guard lookup/insertion/eviction with `threading.RLock`; cache immutable values only. |
| Statistics | Guard counters with the same lock; return immutable snapshots. |
| Events | Publish after sealing; sink failures are captured as health warnings and never alter a result. |

**THREAD-SSF-001:** no caller can observe a partially constructed model. **THREAD-SSF-002:** cache keys include schema, profile revision, canonical inputs, and rounding precision. **THREAD-SSF-003:** statistics are observational and do not participate in scoring.

---

## 14. Serialization

Serialization uses canonical UTF-8 JSON with sorted keys, compact separators, ISO-8601 UTC timestamps, string enum values, and explicit `schema_version`.

- **SER-SSF-001:** Serialize only sealed public output models.
- **SER-SSF-002:** Emit all fields, including empty tuples as arrays.
- **SER-SSF-003:** Encode floats using fixed decimal strings where canonical fingerprint stability requires it.
- **SER-SSF-004:** Never serialize caches, locks, injected clock, event sink, or mutable internals.
- **SER-SSF-005:** Reject unknown required fields only in strict mode; preserve recognized optional extensions in an extension map.
- **SER-SSF-006:** Reject unsupported major schema versions.
- **SER-SSF-007:** Validate ranges and enum values before constructing models.
- **SER-SSF-008:** Canonical output keys use snake_case.
- **SER-SSF-009:** Input fingerprints use SHA-256 over canonical non-derived input JSON.
- **SER-SSF-010:** Serialization errors use SSF.SER codes and do not leak payload secrets in exception messages.

Example envelope:

```json
{"schema_version":"1.0","strategy_id":"iron_condor","overall_score":"74.1250","profile_name":"BALANCED","confidence":{"score":"81.4000","band":"HIGH"}}
```

---

## 15. Health and statistics

Health is operational observability, not a trade quality signal. It must never be used as a score factor.

| Health | Condition | Action |
|---|---|---|
| HEALTHY | No recent internal failures and rejection rate under configured threshold | Continue service |
| DEGRADED | Warning rate or event sink failures exceed threshold | Expose warning; scoring remains deterministic |
| UNHEALTHY | Internal serialization/configuration failure threshold exceeded | Fail closed for affected calls; alert owner |
| DISABLED | Statistics and health intentionally disabled | Return immutable disabled snapshot |

Statistics use monotonic process-local counters and are reset only by constructing a new framework instance. No statistics are persisted by this module.

---

## 16. Error catalog

| Code | Meaning | Caller remediation |
|---|---|---|
| SSF.CFG.001 | Unsupported schema version | Use a supported ScoringFrameworkConfig schema. |
| SSF.CFG.002 | Profile weights do not total 100 | Correct profile configuration. |
| SSF.CFG.003 | Unknown profile | Select a configured exact profile name. |
| SSF.VAL.001 | Empty strategy identifier | Provide a bounded stable ID. |
| SSF.VAL.002 | No factor inputs | Provide required factors. |
| SSF.VAL.003 | Missing required category | Supply category evidence or use an explicitly compatible profile. |
| SSF.VAL.004 | Duplicate factor ID | Make factor IDs unique within category. |
| SSF.VAL.005 | Non-finite numeric input | Supply finite numeric value. |
| SSF.VAL.006 | Raw kind invalid for category | Use documented kind. |
| SSF.VAL.007 | Invalid provenance | Supply non-empty provenance. |
| SSF.VAL.008 | Invalid reliability | Use value in (0,1]. |
| SSF.NORM.001 | Value outside declared domain | Correct source unit or raw kind. |
| SSF.NORM.002 | Unknown label | Configure label mapping or correct label. |
| SSF.NORM.003 | Missing ratio curve | Configure profile curve. |
| SSF.AGG.001 | No usable category weight | Supply valid required factors. |
| SSF.CONF.001 | Confidence component invalid | Treat as implementation defect; fail closed. |
| SSF.SER.001 | Malformed JSON | Supply valid versioned JSON. |
| SSF.SER.002 | Payload schema unsupported | Migrate payload. |
| SSF.SER.003 | Fingerprint mismatch | Reject altered payload and regenerate. |
| SSF.INT.001 | Invariant violation | Fail closed and emit health signal. |

---

## 17. Security

- **SEC-SSF-001:** Treat factor provenance and explanation text as untrusted input; bound lengths and escape only at presentation layer.
- **SEC-SSF-002:** Do not log full payloads at error level; log error code, strategy ID hash, and profile only.
- **SEC-SSF-003:** Reject oversized JSON before decoding into nested structures.
- **SEC-SSF-004:** Use standard-library SHA-256 for integrity fingerprinting, not as authentication.
- **SEC-SSF-005:** Do not accept executable expressions, callable normalizers, or dynamic imports from configuration.
- **SEC-SSF-006:** Never include account, order, token, or client identifiers in score models.
- **SEC-SSF-007:** Event sinks receive sealed public artifacts only and are best-effort.
- **SEC-SSF-008:** Caller authorization belongs outside this pure framework.

---

## 18. Lifecycle and plugin usage

1. A plugin computes its own strategy-specific facts using its permitted upstream inputs.
2. The plugin creates a `FactorInputBundle` with explicit kinds, provenance, and reliability.
3. The plugin selects a documented profile or accepts the configured default.
4. The plugin calls `framework.score(request)` once facts are complete.
5. The immutable score is attached to `TradingSignal` or evaluation report by the owning component.
6. Evaluation Engine ranks reports using its own published ranking rules.
7. Trade Decision Engine consumes the ranking artifact; Risk and Execution remain downstream.

A plugin must not copy normalization formulas or mutate a returned `StrategyScore`. A plugin may score multiple candidate constructions but is responsible for identifying each candidate distinctly.

---

## 19. Event bus topics

| Topic | Payload | Delivery semantics | Restriction |
|---|---|---|---|
| `strategy_score.sealed.v1` | Serialized StrategyScore | Best effort observational | Never consumed to trigger execution directly |
| `strategy_score.rejected.v1` | Code, profile, hashed strategy ID | Best effort observational | No raw factor values |
| `strategy_score.health.v1` | Scoring health snapshot | Best effort observational | Not a trade signal |

The framework does not require an event bus. An event sink is injected and cannot affect validation, aggregation, or confidence.

---

## 20. Integrations

| Consumer | May do | Must not infer |
|---|---|---|
| BaseStrategy/plugin | Supply extracted facts; attach sealed score | That score selects a strategy or approves trade |
| Strategy Evaluation Engine | Use overall score/confidence as a documented ranking input | A new universal taxonomy or alternate normalization |
| Strategy Engine | Consume sealed evidence during signal aggregation | That framework replaces conflict resolution |
| Trade Decision Engine | Compare evaluation reports objectively | That a high score overrides risk or execution constraints |

### 20.1 Evaluation Engine compatibility

`StrategyScore.overall_score` shares the 0–100 suitability scale with evaluation-engine `suitability_score`. They are not automatically equal: evaluation suitability may combine plugin-specific evidence, while this score is the universal factor aggregation. An integration adapter must name its combination rule and preserve both source values.

---

## 21. Testing requirements

Tests reside in `tests/test_strategy_scoring_framework.py`. Minimum line and branch coverage is 95 percent. Tests must use a fake `FactorInputBundle`, fixed UTC clock, deterministic profiles, and no market, network, broker, or database dependency.

| Test ID | Required test |
|---|---|
| TST-SSF-001 | Default balanced score with all seven categories. |
| TST-SSF-002 | Each raw kind conversion at boundaries and midpoint. |
| TST-SSF-003 | Trend signed-unit conversion for -1, 0, and 1. |
| TST-SSF-004 | Label mapping success and unknown-label rejection. |
| TST-SSF-005 | Ratio interpolation and endpoint policy. |
| TST-SSF-006 | Required category omission rejection. |
| TST-SSF-007 | Optional category omission redistribution. |
| TST-SSF-008 | Profile total and unknown-profile validation. |
| TST-SSF-009 | Reliability-weighted category aggregation. |
| TST-SSF-010 | Event-risk adverse and safe mappings. |
| TST-SSF-011 | Confidence band boundaries 44.9999, 45, 70, and 85. |
| TST-SSF-012 | Agreement penalties from divergent factor scores. |
| TST-SSF-013 | Explanation ordering, strengths, concerns, and stable formatting. |
| TST-SSF-014 | Frozen-model mutation failure. |
| TST-SSF-015 | Canonical JSON round trip and fingerprint verification. |
| TST-SSF-016 | Malformed payload and unknown schema rejection. |
| TST-SSF-017 | Non-finite, out-of-range, and boolean numeric rejection. |
| TST-SSF-018 | Concurrent cache/statistics access with deterministic results. |
| TST-SSF-019 | Event sink exception isolation. |
| TST-SSF-020 | No forbidden dependency imports. |

---

## 22. Implementation checklist

- [ ] Define all public enums and frozen dataclasses with Google-style docstrings.
- [ ] Implement pure validator and normalized intermediate model.
- [ ] Implement Decimal-based canonical normalizers.
- [ ] Implement immutable profile registry and configuration validation.
- [ ] Implement category-first aggregation.
- [ ] Implement confidence calculator and bands.
- [ ] Implement stable explanation templates.
- [ ] Implement canonical JSON serializer/deserializer.
- [ ] Implement optional lock-protected cache/statistics/health.
- [ ] Implement optional event sink isolation.
- [ ] Add complete unit tests and dependency-boundary test.
- [ ] Run formatter, type checker, unit tests, and coverage report.
- [ ] Update CHANGELOG when implementation lands.

---

## 23. Definition of Done

- [ ] All required models use the exact public names: StrategyScore, FactorScore, ConfidenceReport, and StrategyExplanation.
- [ ] ScoringFrameworkConfig, FactorCategory, StrategyScoringFramework/StrategyScorer, ValidationResult, ScoringStatistics, serializers, and SSF error codes exist.
- [ ] Every normative category has validation, normalization, weight, aggregation, explanation, and test coverage.
- [ ] No market snapshot fetching/parsing, indicator calculation, broker call, order action, strategy selection, conflict resolution, or risk-limit enforcement exists.
- [ ] Score values are deterministic floats in 0–100 sealed to four decimals.
- [ ] Thread behavior meets the documented stateless/locked-cache contract.
- [ ] Versioned JSON round trip is tested.
- [ ] Coverage is at least 95 percent.
- [ ] Public APIs have Google-style docstrings and immutable models.

---

## 24. Non-goals

- This specification does not add market data retrieval or marketsnapshot parsing.
- This specification does not add indicator, volatility-surface, or greeks calculation.
- This specification does not add regime detection or trend classification.
- This specification does not add liquidity measurement or option-chain analysis.
- This specification does not add strategy implementation or signal generation.
- This specification does not add strategy selection or conflict resolution.
- This specification does not add evaluation report ownership or ranking policy.
- This specification does not add trade decision approval.
- This specification does not add risk-limit, margin, position-size, or exposure enforcement.
- This specification does not add portfolio construction or allocation.
- This specification does not add order construction, routing, brokerage, or execution.
- This specification does not add authentication, credential storage, or configuration-file loading.
- This specification does not add persistence outside an injected audit/event adapter.
- This specification does not add mutation of strategy plugin state.
- This specification does not add live-time scheduling, polling, or retry loops.
- This specification does not add predictive modelling or model inference.
- This specification does not add backtesting orchestration.
- This specification does not add market-event calendar acquisition.
- This specification does not add guaranteeing expected return or trade profitability.
- This specification does not add replacing any frozen pipeline component.

---

# Appendices

## Appendix A — Worked Market Regime Examples

These examples demonstrate supplied `Market Regime` evidence. Values are illustrative normalization inputs, not market calculations or trading recommendations.

| Case | Raw kind | Raw input | Normalized | Explanation outcome |
|---|---|---|---|---|
| BULLISH_TREND | `SCORE_0_100` | 85 | 85.0000 | Strength |
| BEARISH_TREND | `SCORE_0_100` | 20 | 20.0000 | Concern |
| RANGE_BOUND | `SCORE_0_100` | 70 | 70.0000 | Strength |
| HIGH_UNCERTAINTY | `SCORE_0_100` | 25 | 25.0000 | Concern |

**A-1:** A plugin records `BULLISH_TREND` with explicit provenance and a declared raw kind. The framework seals `85.0000` only after validation; it does not verify the underlying market interpretation.
**A-2:** A plugin records `BEARISH_TREND` with explicit provenance and a declared raw kind. The framework seals `20.0000` only after validation; it does not verify the underlying market interpretation.
**A-3:** A plugin records `RANGE_BOUND` with explicit provenance and a declared raw kind. The framework seals `70.0000` only after validation; it does not verify the underlying market interpretation.
**A-4:** A plugin records `HIGH_UNCERTAINTY` with explicit provenance and a declared raw kind. The framework seals `25.0000` only after validation; it does not verify the underlying market interpretation.

## Appendix B — Worked Trend Alignment Examples

These examples demonstrate supplied `Trend Alignment` evidence. Values are illustrative normalization inputs, not market calculations or trading recommendations.

| Case | Raw kind | Raw input | Normalized | Explanation outcome |
|---|---|---|---|---|
| short-put alignment | `SCORE_0_100` | 90 | 90.0000 | Strength |
| long-call alignment | `SCORE_0_100` | 80 | 80.0000 | Strength |
| neutral structure | `SCORE_0_100` | 65 | 65.0000 | Neutral evidence |
| opposing direction | `SCORE_0_100` | 15 | 15.0000 | Concern |

**B-1:** A plugin records `short-put alignment` with explicit provenance and a declared raw kind. The framework seals `90.0000` only after validation; it does not verify the underlying market interpretation.
**B-2:** A plugin records `long-call alignment` with explicit provenance and a declared raw kind. The framework seals `80.0000` only after validation; it does not verify the underlying market interpretation.
**B-3:** A plugin records `neutral structure` with explicit provenance and a declared raw kind. The framework seals `65.0000` only after validation; it does not verify the underlying market interpretation.
**B-4:** A plugin records `opposing direction` with explicit provenance and a declared raw kind. The framework seals `15.0000` only after validation; it does not verify the underlying market interpretation.

## Appendix C — Worked Volatility Examples

These examples demonstrate supplied `Volatility` evidence. Values are illustrative normalization inputs, not market calculations or trading recommendations.

| Case | Raw kind | Raw input | Normalized | Explanation outcome |
|---|---|---|---|---|
| premium-selling elevated IV | `SCORE_0_100` | 88 | 88.0000 | Strength |
| long-vol compressed IV | `SCORE_0_100` | 82 | 82.0000 | Strength |
| neutral IV fit | `SCORE_0_100` | 60 | 60.0000 | Neutral evidence |
| mismatched IV | `SCORE_0_100` | 20 | 20.0000 | Concern |

**C-1:** A plugin records `premium-selling elevated IV` with explicit provenance and a declared raw kind. The framework seals `88.0000` only after validation; it does not verify the underlying market interpretation.
**C-2:** A plugin records `long-vol compressed IV` with explicit provenance and a declared raw kind. The framework seals `82.0000` only after validation; it does not verify the underlying market interpretation.
**C-3:** A plugin records `neutral IV fit` with explicit provenance and a declared raw kind. The framework seals `60.0000` only after validation; it does not verify the underlying market interpretation.
**C-4:** A plugin records `mismatched IV` with explicit provenance and a declared raw kind. The framework seals `20.0000` only after validation; it does not verify the underlying market interpretation.

## Appendix D — Worked Liquidity Examples

These examples demonstrate supplied `Liquidity` evidence. Values are illustrative normalization inputs, not market calculations or trading recommendations.

| Case | Raw kind | Raw input | Normalized | Explanation outcome |
|---|---|---|---|---|
| tight spread/deep book | `SCORE_0_100` | 95 | 95.0000 | Strength |
| acceptable spread | `SCORE_0_100` | 70 | 70.0000 | Strength |
| thin depth | `SCORE_0_100` | 42 | 42.0000 | Concern |
| wide spread | `SCORE_0_100` | 10 | 10.0000 | Concern |

**D-1:** A plugin records `tight spread/deep book` with explicit provenance and a declared raw kind. The framework seals `95.0000` only after validation; it does not verify the underlying market interpretation.
**D-2:** A plugin records `acceptable spread` with explicit provenance and a declared raw kind. The framework seals `70.0000` only after validation; it does not verify the underlying market interpretation.
**D-3:** A plugin records `thin depth` with explicit provenance and a declared raw kind. The framework seals `42.0000` only after validation; it does not verify the underlying market interpretation.
**D-4:** A plugin records `wide spread` with explicit provenance and a declared raw kind. The framework seals `10.0000` only after validation; it does not verify the underlying market interpretation.

## Appendix E — Worked Greeks Examples

These examples demonstrate supplied `Greeks` evidence. Values are illustrative normalization inputs, not market calculations or trading recommendations.

| Case | Raw kind | Raw input | Normalized | Explanation outcome |
|---|---|---|---|---|
| defined delta exposure | `SCORE_0_100` | 85 | 85.0000 | Strength |
| theta aligned | `SCORE_0_100` | 78 | 78.0000 | Strength |
| vega exposure bounded | `SCORE_0_100` | 68 | 68.0000 | Neutral evidence |
| gamma mismatch | `SCORE_0_100` | 25 | 25.0000 | Concern |

**E-1:** A plugin records `defined delta exposure` with explicit provenance and a declared raw kind. The framework seals `85.0000` only after validation; it does not verify the underlying market interpretation.
**E-2:** A plugin records `theta aligned` with explicit provenance and a declared raw kind. The framework seals `78.0000` only after validation; it does not verify the underlying market interpretation.
**E-3:** A plugin records `vega exposure bounded` with explicit provenance and a declared raw kind. The framework seals `68.0000` only after validation; it does not verify the underlying market interpretation.
**E-4:** A plugin records `gamma mismatch` with explicit provenance and a declared raw kind. The framework seals `25.0000` only after validation; it does not verify the underlying market interpretation.

## Appendix F — Worked Risk/Reward Examples

These examples demonstrate supplied `Risk Reward` evidence. Values are illustrative normalization inputs, not market calculations or trading recommendations.

| Case | Raw kind | Raw input | Normalized | Explanation outcome |
|---|---|---|---|---|
| defined-risk favorable payoff | `SCORE_0_100` | 92 | 92.0000 | Strength |
| credit fits max loss | `SCORE_0_100` | 76 | 76.0000 | Strength |
| marginal payoff | `SCORE_0_100` | 48 | 48.0000 | Neutral evidence |
| unbounded mismatch | `SCORE_0_100` | 12 | 12.0000 | Concern |

**F-1:** A plugin records `defined-risk favorable payoff` with explicit provenance and a declared raw kind. The framework seals `92.0000` only after validation; it does not verify the underlying market interpretation.
**F-2:** A plugin records `credit fits max loss` with explicit provenance and a declared raw kind. The framework seals `76.0000` only after validation; it does not verify the underlying market interpretation.
**F-3:** A plugin records `marginal payoff` with explicit provenance and a declared raw kind. The framework seals `48.0000` only after validation; it does not verify the underlying market interpretation.
**F-4:** A plugin records `unbounded mismatch` with explicit provenance and a declared raw kind. The framework seals `12.0000` only after validation; it does not verify the underlying market interpretation.

## Appendix G — Worked Event Risk Examples

These examples demonstrate supplied `Event Risk` evidence. Values are illustrative normalization inputs, not market calculations or trading recommendations.

| Case | Raw kind | Raw input | Normalized | Explanation outcome |
|---|---|---|---|---|
| no known event | `SCORE_0_100` | 95 | 95.0000 | Strength |
| event hedged | `SCORE_0_100` | 65 | 65.0000 | Neutral evidence |
| event nearby | `SCORE_0_100` | 35 | 35.0000 | Concern |
| unmitigated binary event | `SCORE_0_100` | 5 | 5.0000 | Concern |

**G-1:** A plugin records `no known event` with explicit provenance and a declared raw kind. The framework seals `95.0000` only after validation; it does not verify the underlying market interpretation.
**G-2:** A plugin records `event hedged` with explicit provenance and a declared raw kind. The framework seals `65.0000` only after validation; it does not verify the underlying market interpretation.
**G-3:** A plugin records `event nearby` with explicit provenance and a declared raw kind. The framework seals `35.0000` only after validation; it does not verify the underlying market interpretation.
**G-4:** A plugin records `unmitigated binary event` with explicit provenance and a declared raw kind. The framework seals `5.0000` only after validation; it does not verify the underlying market interpretation.

## Appendix H — Weight Profile Tables

### `BALANCED`

| Category | Weight | Rationale |
|---|---|---|
| Market Regime | 15.0000 | General comparable suitability |
| Trend Alignment | 15.0000 | General comparable suitability |
| Volatility | 15.0000 | General comparable suitability |
| Liquidity | 15.0000 | General comparable suitability |
| Greeks | 10.0000 | General comparable suitability |
| Risk Reward | 20.0000 | General comparable suitability |
| Event Risk | 10.0000 | General comparable suitability |

`BALANCED` is an immutable revisioned profile. It changes evidence emphasis only; it never changes downstream risk policy.

### `PREMIUM_SELLING`

| Category | Weight | Rationale |
|---|---|---|
| Market Regime | 15.0000 | Volatility and liquidity-aware premium strategies |
| Trend Alignment | 10.0000 | Volatility and liquidity-aware premium strategies |
| Volatility | 20.0000 | Volatility and liquidity-aware premium strategies |
| Liquidity | 20.0000 | Volatility and liquidity-aware premium strategies |
| Greeks | 10.0000 | Volatility and liquidity-aware premium strategies |
| Risk Reward | 15.0000 | Volatility and liquidity-aware premium strategies |
| Event Risk | 10.0000 | Volatility and liquidity-aware premium strategies |

`PREMIUM_SELLING` is an immutable revisioned profile. It changes evidence emphasis only; it never changes downstream risk policy.

### `DEFINED_RISK`

| Category | Weight | Rationale |
|---|---|---|
| Market Regime | 15.0000 | Payoff/risk-reward emphasis |
| Trend Alignment | 15.0000 | Payoff/risk-reward emphasis |
| Volatility | 15.0000 | Payoff/risk-reward emphasis |
| Liquidity | 10.0000 | Payoff/risk-reward emphasis |
| Greeks | 10.0000 | Payoff/risk-reward emphasis |
| Risk Reward | 25.0000 | Payoff/risk-reward emphasis |
| Event Risk | 10.0000 | Payoff/risk-reward emphasis |

`DEFINED_RISK` is an immutable revisioned profile. It changes evidence emphasis only; it never changes downstream risk policy.

### `DIRECTIONAL`

| Category | Weight | Rationale |
|---|---|---|
| Market Regime | 10.0000 | Trend-aligned directional strategies |
| Trend Alignment | 25.0000 | Trend-aligned directional strategies |
| Volatility | 10.0000 | Trend-aligned directional strategies |
| Liquidity | 15.0000 | Trend-aligned directional strategies |
| Greeks | 10.0000 | Trend-aligned directional strategies |
| Risk Reward | 20.0000 | Trend-aligned directional strategies |
| Event Risk | 10.0000 | Trend-aligned directional strategies |

`DIRECTIONAL` is an immutable revisioned profile. It changes evidence emphasis only; it never changes downstream risk policy.

### `EVENT_CAUTION`

| Category | Weight | Rationale |
|---|---|---|
| Market Regime | 10.0000 | Strong event-risk sensitivity |
| Trend Alignment | 10.0000 | Strong event-risk sensitivity |
| Volatility | 15.0000 | Strong event-risk sensitivity |
| Liquidity | 10.0000 | Strong event-risk sensitivity |
| Greeks | 10.0000 | Strong event-risk sensitivity |
| Risk Reward | 15.0000 | Strong event-risk sensitivity |
| Event Risk | 30.0000 | Strong event-risk sensitivity |

`EVENT_CAUTION` is an immutable revisioned profile. It changes evidence emphasis only; it never changes downstream risk policy.

## Appendix I — Aggregation Pseudocode

```python
def aggregate(categories, effective_weights, decimals):
    weighted_total = Decimal('0')
    for category in CANONICAL_CATEGORY_ORDER:
        score = weighted_mean(categories[category].valid_factors)
        weighted_total += score * effective_weights[category] / Decimal('100')
    return seal(bound_epsilon(weighted_total), decimals)
```

I-1. Aggregate stage 1 preserves category order, validates finite Decimal operands, and records an immutable audit value before proceeding.
I-2. Aggregate stage 2 preserves category order, validates finite Decimal operands, and records an immutable audit value before proceeding.
I-3. Aggregate stage 3 preserves category order, validates finite Decimal operands, and records an immutable audit value before proceeding.
I-4. Aggregate stage 4 preserves category order, validates finite Decimal operands, and records an immutable audit value before proceeding.
I-5. Aggregate stage 5 preserves category order, validates finite Decimal operands, and records an immutable audit value before proceeding.
I-6. Aggregate stage 6 preserves category order, validates finite Decimal operands, and records an immutable audit value before proceeding.
I-7. Aggregate stage 7 preserves category order, validates finite Decimal operands, and records an immutable audit value before proceeding.
I-8. Aggregate stage 8 preserves category order, validates finite Decimal operands, and records an immutable audit value before proceeding.
I-9. Aggregate stage 9 preserves category order, validates finite Decimal operands, and records an immutable audit value before proceeding.
I-10. Aggregate stage 10 preserves category order, validates finite Decimal operands, and records an immutable audit value before proceeding.
I-11. Aggregate stage 11 preserves category order, validates finite Decimal operands, and records an immutable audit value before proceeding.
I-12. Aggregate stage 12 preserves category order, validates finite Decimal operands, and records an immutable audit value before proceeding.
## Appendix J — Confidence Curves

| Coverage | Agreement | Data quality | Unpenalized confidence | Band |
|---|---|---|---|---|
| 100 | 100 | 100 | 100.0000 | VERY_HIGH |
| 90 | 85 | 90 | 88.5000 | VERY_HIGH |
| 80 | 75 | 80 | 78.5000 | HIGH |
| 70 | 65 | 75 | 69.5000 | MEDIUM |
| 45 | 50 | 55 | 48.5000 | MEDIUM |
| 30 | 40 | 50 | 38.5000 | LOW |

J-1. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-2. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-3. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-4. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-5. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-6. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-7. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-8. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-9. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-10. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-11. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-12. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-13. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-14. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.
J-15. Confidence curve checkpoint: confidence is evidence quality, not a probability of trade profit; penalties are applied after the three component blend and are capped by configuration.

## Appendix K — Explanation Templates

| Template ID | Stable template |
|---|---|
| EXPL-TPL-001 | Factor {factor_id} in {category} normalized to {score}; contribution is {contribution}. |
| EXPL-TPL-002 | {category} is a strength because its normalized score is {score}. |
| EXPL-TPL-003 | {category} is a concern because its normalized score is {score}. |
| EXPL-TPL-004 | {category} was omitted under allowed optional-factor policy; weights were redistributed. |
| EXPL-TPL-005 | Confidence is {band} because coverage={coverage}, agreement={agreement}, data_quality={data_quality}. |

K-1. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-2. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-3. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-4. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-5. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-6. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-7. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-8. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-9. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-10. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-11. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-12. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-13. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-14. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-15. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.
K-16. Template invariant: prose reports sealed values only and contains no inferred order, market-data, or risk-approval statement.

## Appendix L — Failure Matrix

| Failure | Framework action | Code | Result |
|---|---|---|---|
| Missing required category | Reject | SSF.VAL.003 | No score |
| NaN raw numeric | Reject | SSF.VAL.005 | No score |
| Unknown label | Reject | SSF.NORM.002 | No score |
| Optional omission | Warn/continue | VAL warning | Redistribute if enabled |
| Event sink failure | Continue | health warning | Sealed score unchanged |
| Cache contention | Serialize lock access | none | Equivalent result |
| Payload fingerprint mismatch | Reject | SSF.SER.003 | No deserialized score |

L-1. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-2. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-3. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-4. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-5. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-6. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-7. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-8. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-9. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-10. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-11. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-12. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-13. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-14. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-15. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-16. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.
L-17. Failure handling invariant: no invalid or ambiguous input is silently converted into positive suitability evidence.

## Appendix M — Concurrency Sketches

```text
Thread A: validate → normalize → aggregate → seal ─┐
Thread B: validate → normalize → aggregate → seal ─┼─ pure, independent results
Thread C: statistics() ─ RLock snapshot ───────────┤
Cache: lookup/insert/evict only under RLock ────────┘
```

M-1. Concurrency rule: thread schedule 1 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-2. Concurrency rule: thread schedule 2 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-3. Concurrency rule: thread schedule 3 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-4. Concurrency rule: thread schedule 4 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-5. Concurrency rule: thread schedule 5 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-6. Concurrency rule: thread schedule 6 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-7. Concurrency rule: thread schedule 7 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-8. Concurrency rule: thread schedule 8 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-9. Concurrency rule: thread schedule 9 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-10. Concurrency rule: thread schedule 10 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-11. Concurrency rule: thread schedule 11 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-12. Concurrency rule: thread schedule 12 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-13. Concurrency rule: thread schedule 13 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-14. Concurrency rule: thread schedule 14 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-15. Concurrency rule: thread schedule 15 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-16. Concurrency rule: thread schedule 16 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
M-17. Concurrency rule: thread schedule 17 cannot change canonical ordering, arithmetic inputs, rounded output, or explanation ordering.
## Appendix N — Glossary

| Term | Definition |
|---|---|
| Aggregate | Weighted universal suitability value in 0–100. |
| Category | One normative evidence family owned by this framework. |
| Confidence | Evidence quality summary, not profitability probability. |
| Factor | One caller-supplied already-computed item of evidence. |
| Profile | Versioned immutable category-weight and mapping configuration. |
| Seal | Final validation, rounding, fingerprinting, and immutable construction. |
| Suitability | Comparative score evidence; not trade authorization. |
| Provenance | Declared source identity of a supplied factor. |

N-1. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-2. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-3. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-4. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-5. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-6. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-7. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-8. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-9. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-10. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-11. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-12. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-13. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-14. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-15. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-16. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.
N-17. Glossary usage note: terminology is normative in this module and intentionally does not redefine terms owned by the Evaluation or Trade Decision Engine.

## Appendix O — Migration from EvaluationScorer and ConfidenceScorer

| Legacy concern | Migration action |
|---|---|
| Existing `ConfidenceScorer` concept | Map confidence calculation to `ConfidenceReport`; retain Strategy Engine conflict ownership. |
| Existing EvaluationScorer suitability input | Adapt to `FactorInputBundle`; preserve original field for compatibility. |
| Plugin-local weights | Replace with named immutable profile selection. |
| Ad hoc explanations | Replace with StrategyExplanation templates. |
| Mutable score dict | Replace with frozen StrategyScore and serializer. |

O-1. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-2. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-3. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-4. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-5. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-6. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-7. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-8. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-9. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-10. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-11. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-12. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-13. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-14. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-15. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-16. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-17. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.
O-18. Migration step: introduce an adapter in the owning consumer, dual-write old and sealed artifacts during observation, compare results, then retire only documented duplicate scoring math.

## Appendix P — Benchmarks

| Benchmark | Target | Method |
|---|---|---|
| Single seven-category score | p95 under 2 ms | Warm local process, cache disabled |
| Canonical serialization | p95 under 1 ms | Representative full explanation |
| Validation rejection | p95 under 1 ms | Missing category case |
| Concurrent 16 callers | No output divergence | Fixed inputs/clock |

P-1. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-2. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-3. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-4. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-5. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-6. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-7. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-8. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-9. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-10. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-11. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-12. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-13. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-14. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-15. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-16. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-17. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-18. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.
P-19. Benchmark rule: measure framework-only CPU work with fixed generated factor bundles; do not include market, broker, event-bus, disk, or network latency.

## Appendix Q — Profile Defaults

### BALANCED
- Category weights: `15/15/15/15/10/20/10`.
- Default intent: General comparable suitability.
- Required categories remain required unless this immutable profile explicitly marks one optional.
- Revision changes require a migration note and new fingerprint.

### PREMIUM_SELLING
- Category weights: `15/10/20/20/10/15/10`.
- Default intent: Volatility and liquidity-aware premium strategies.
- Required categories remain required unless this immutable profile explicitly marks one optional.
- Revision changes require a migration note and new fingerprint.

### DEFINED_RISK
- Category weights: `15/15/15/10/10/25/10`.
- Default intent: Payoff/risk-reward emphasis.
- Required categories remain required unless this immutable profile explicitly marks one optional.
- Revision changes require a migration note and new fingerprint.

### DIRECTIONAL
- Category weights: `10/25/10/15/10/20/10`.
- Default intent: Trend-aligned directional strategies.
- Required categories remain required unless this immutable profile explicitly marks one optional.
- Revision changes require a migration note and new fingerprint.

### EVENT_CAUTION
- Category weights: `10/10/15/10/10/15/30`.
- Default intent: Strong event-risk sensitivity.
- Required categories remain required unless this immutable profile explicitly marks one optional.
- Revision changes require a migration note and new fingerprint.

## Appendix R — Factor Input Schemas

```json
{"strategy_id":"string","profile_name":"BALANCED","factors":[{"category":"TREND_ALIGNMENT","factor_id":"trend_alignment","raw_value":0.72,"raw_value_kind":"UNIT_INTERVAL","provenance":"plugin:v1","reliability":1.0}]}
```

R-1. Schema rule: factor input field 1 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-2. Schema rule: factor input field 2 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-3. Schema rule: factor input field 3 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-4. Schema rule: factor input field 4 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-5. Schema rule: factor input field 5 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-6. Schema rule: factor input field 6 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-7. Schema rule: factor input field 7 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-8. Schema rule: factor input field 8 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-9. Schema rule: factor input field 9 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-10. Schema rule: factor input field 10 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-11. Schema rule: factor input field 11 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-12. Schema rule: factor input field 12 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-13. Schema rule: factor input field 13 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-14. Schema rule: factor input field 14 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-15. Schema rule: factor input field 15 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-16. Schema rule: factor input field 16 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-17. Schema rule: factor input field 17 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-18. Schema rule: factor input field 18 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
R-19. Schema rule: factor input field 19 is bounded, explicit, and carries no raw market snapshot, broker credential, or executable normalization expression.
## Appendix S — Ranking Compatibility Notes

S-1. Use `(overall_score, confidence.score)` only as named inputs to evaluation ranking.
S-2. Keep StrategyEvaluationEngine tie-breakers separate and documented there.
S-3. Never rank directly inside this framework.
S-4. A low confidence score must remain observable even when overall suitability is high.
S-5. Evaluation reports preserve profile name and score fingerprint for audit correlation.
S-6. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-7. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-8. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-9. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-10. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-11. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-12. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-13. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-14. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-15. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-16. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-17. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-18. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-19. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-20. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.
S-21. Compatibility invariant: the shared 0–100 scale enables comparison, but consumer-specific ranking policy remains outside this module.

## Appendix T — Serialization Examples

T-1. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-2. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-3. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-4. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-5. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-6. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-7. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-8. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-9. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-10. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-11. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-12. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-13. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-14. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-15. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-16. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-17. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-18. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.
T-19. Serialization example invariant: schema `1.0` uses canonical field order for fingerprint computation, emits enum names as strings, and validates every decoded numerical range before construction.

## Appendix U — Validation Decision Tables

| Condition | Decision | Outcome |
|---|---|---|
| Known category / finite raw / valid kind | Accept | Normalize |
| Known category / missing raw | Reject | SSF.VAL.005 |
| Unknown category | Reject | SSF.VAL.006 |
| Optional missing category | Warn | Redistribute or preserve omission |
| Unsupported profile revision | Reject | SSF.CFG.003 |
| Valid payload wrong fingerprint | Reject | SSF.SER.003 |

U-1. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-2. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-3. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-4. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-5. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-6. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-7. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-8. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-9. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-10. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-11. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-12. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-13. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-14. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-15. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-16. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.
U-17. Decision-table rule: validation makes a single explicit terminal decision and never falls back to a guessed score.

## Appendix V — Observability Runbook

V-1. Inspect health snapshot and recent error-code counts.
V-2. Confirm profile revision matches deployment expectation.
V-3. Reproduce with fixed clock and canonical input fixture.
V-4. Classify source factor issue versus framework invariant violation.
V-5. Do not edit weights in production to compensate for source failure.
V-6. Escalate SSF.INT codes as implementation defects.
V-7. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-8. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-9. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-10. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-11. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-12. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-13. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-14. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-15. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-16. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-17. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-18. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-19. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.
V-20. Operational rule: observability is diagnostic only and does not create a bypass around validation or downstream risk governance.

## Appendix W — Testing Fixtures

| Fixture | Purpose |
|---|---|
| `fake_factor_bundle()` | All seven valid canonical factors |
| `fixed_clock()` | UTC `2026-01-01T00:00:00Z` |
| `balanced_config()` | Revisioned deterministic profile registry |
| `failing_event_sink()` | Raises to test isolation |
| `invalid_numeric_bundle()` | Contains NaN/out-of-range cases |

W-1. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-2. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-3. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-4. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-5. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-6. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-7. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-8. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-9. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-10. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-11. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-12. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-13. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-14. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-15. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-16. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-17. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.
W-18. Fixture policy: fixtures contain already-extracted primitive values and never construct or access MarketSnapshot, broker clients, indicator engines, or live clocks.

## Appendix X — Operational Migration Notes

X-1. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-2. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-3. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-4. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-5. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-6. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-7. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-8. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-9. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-10. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-11. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-12. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-13. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-14. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-15. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-16. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-17. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-18. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-19. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-20. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.
X-21. Migration note: deploy profile registry and serializer first, shadow-score plugin outputs with a fixed clock, compare canonical artifacts, then enable consumers only after documented agreement; no pipeline ownership changes occur.

## Appendix Y — Open Notes and Explicit Deferrals

Y-1. Future profile calibration is configuration governance, not runtime learning.
Y-2. Cross-strategy correlation remains outside this module.
Y-3. Calendar source reliability is owned by event-risk producer.
Y-4. Score persistence is owned by an external audit repository.
Y-5. Thresholds for trade action remain owned by downstream decision/risk components.
Y-6. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-7. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-8. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-9. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-10. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-11. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-12. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-13. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-14. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-15. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-16. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-17. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-18. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.
Y-19. Explicit deferral: this item does not weaken any frozen BOUNDARY-SSF rule and does not imply a redesign of the pipeline.

## Appendix Z — Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-05 | Initial production implementation contract for universal strategy scoring. |

Z-1. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-2. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-3. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-4. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-5. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-6. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-7. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-8. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-9. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-10. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-11. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-12. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-13. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-14. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-15. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-16. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-17. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-18. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.
Z-19. Change-control rule: any alteration to category taxonomy, normalization curve, aggregation formula, confidence band, or serialized field requires a versioned specification and migration assessment.

## Appendix Z.1 — Normative control matrix

### Z.1.01 Input identity

**Control intent.** Strategy id and factor ids are bounded, present, and canonical before processing.
**Control ID.** `CTRL-SSF-001` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.02 Category ownership

**Control intent.** Each input belongs to exactly one factorcategory owned by this module.
**Control ID.** `CTRL-SSF-002` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.03 Raw kind declaration

**Control intent.** Each raw value declares its unit and is never guessed from magnitude.
**Control ID.** `CTRL-SSF-003` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.04 Numeric finiteness

**Control intent.** All numeric values reject nan and both infinities.
**Control ID.** `CTRL-SSF-004` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.05 Unit boundary

**Control intent.** The declared kind enforces its inclusive documented range.
**Control ID.** `CTRL-SSF-005` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.06 Label map

**Control intent.** Labels resolve by immutable profile revision only.
**Control ID.** `CTRL-SSF-006` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.07 Ratio curve

**Control intent.** Ratio interpolation uses only configured monotonic points.
**Control ID.** `CTRL-SSF-007` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.08 Provenance

**Control intent.** Source identity is bounded and preserved for audit.
**Control ID.** `CTRL-SSF-008` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.09 Reliability

**Control intent.** Per-factor reliability is explicit, finite, and bounded.
**Control ID.** `CTRL-SSF-009` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.10 Category coverage

**Control intent.** Every required category has usable evidence.
**Control ID.** `CTRL-SSF-010` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.11 Optional omission

**Control intent.** Only profile-declared optional omission may redistribute weight.
**Control ID.** `CTRL-SSF-011` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.12 Weight integrity

**Control intent.** Effective weights reconcile to exactly 100 at seal precision.
**Control ID.** `CTRL-SSF-012` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.13 Category aggregation

**Control intent.** Many factors cannot exceed their category allocation.
**Control ID.** `CTRL-SSF-013` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.14 Penalty cap

**Control intent.** Configured penalties remain bounded and explicit.
**Control ID.** `CTRL-SSF-014` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.15 Score range

**Control intent.** Aggregate suitability remains a finite 0–100 value.
**Control ID.** `CTRL-SSF-015` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.16 Confidence coverage

**Control intent.** Confidence records represented effective evidence weight.
**Control ID.** `CTRL-SSF-016` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.17 Confidence agreement

**Control intent.** Dispersion is measured against sealed aggregate evidence.
**Control ID.** `CTRL-SSF-017` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.18 Confidence quality

**Control intent.** Warnings reduce quality without changing raw facts.
**Control ID.** `CTRL-SSF-018` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.19 Confidence band

**Control intent.** Threshold assignment uses inclusive lower bounds.
**Control ID.** `CTRL-SSF-019` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.20 Explanation order

**Control intent.** Narratives follow canonical category and factor ordering.
**Control ID.** `CTRL-SSF-020` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.21 Explanation safety

**Control intent.** Templates state evidence without recommending a trade.
**Control ID.** `CTRL-SSF-021` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.22 Immutability

**Control intent.** All emitted models are frozen and nested collections immutable.
**Control ID.** `CTRL-SSF-022` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.23 Fingerprint

**Control intent.** Canonical non-derived request input determines sha-256 identity.
**Control ID.** `CTRL-SSF-023` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.24 Serialization

**Control intent.** Json carries explicit schema version and validated values.
**Control ID.** `CTRL-SSF-024` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.25 Cache isolation

**Control intent.** Optional cache stores only immutable sealed artifacts under lock.
**Control ID.** `CTRL-SSF-025` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.26 Statistics isolation

**Control intent.** Operational counters cannot influence scoring arithmetic.
**Control ID.** `CTRL-SSF-026` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.27 Event isolation

**Control intent.** Event sink errors do not alter terminal scoring result.
**Control ID.** `CTRL-SSF-027` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.28 Dependency boundary

**Control intent.** No market, broker, indicator, risk, or execution dependency exists.
**Control ID.** `CTRL-SSF-028` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

### Z.1.29 Error stability

**Control intent.** Every rejected condition exposes a documented stable ssf code and caller remediation.
**Control ID.** `CTRL-SSF-029` is evaluated deterministically for every applicable score request.
**Failure behavior.** A violation produces a stable SSF error or validation issue; it never yields a substituted positive score.
**Audit evidence.** The sealed artifact retains the relevant normalized value, profile revision, validation note, or fingerprint needed to reproduce the outcome.
**Test evidence.** The unit suite includes an acceptance and a rejection case using fixed primitives and a fixed UTC clock.
**Boundary evidence.** This control consumes caller-supplied facts only and does not calculate, fetch, select, approve, or execute anything.
**Concurrency evidence.** Evaluation uses local immutable intermediates; optional observational state is locked and cannot affect this control.
**Compatibility evidence.** Consumers may read the resulting artifact but cannot redefine this control through a ranking or decision policy.
**Change evidence.** Any change requires a versioned profile or specification review, a migration assessment, and expanded regression fixtures.

---

## Final compliance statement

This specification freezes `strategy/strategy_scoring_framework.py` as a pure universal scoring component. It standardizes evidence comparison while preserving ownership of strategy logic, evaluation ranking, signal conflict resolution, trade decisions, risk controls, and execution in their existing pipeline components.

