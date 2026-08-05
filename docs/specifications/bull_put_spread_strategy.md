# Bull Put Spread Strategy — Software Engineering Specification

| Field | Value |
|---|---|
| Module | `strategy/bull_put_spread_strategy.py` |
| Document version | `1.0.0` |
| Status | Implementation contract |
| Owner | THETA AI TRADER Core Platform |
| Last updated | 2026-08-05 |
| Strategy identifier | `bull_put_spread` |
| Strategy family | `bull_put_spread` |
| Risk profile | Defined-risk bullish credit vertical; finite wing-capped loss |

---

## 1. Purpose

`strategy/bull_put_spread_strategy.py` is the deterministic, read-only Bull Put
Spread strategy plugin for THETA AI TRADER v1.0.

It answers the following bounded question:

> Given an injected `MarketSnapshot`, optional historical and portfolio
> context, an optional risk-profile preference, and an immutable
> `BullPutSpreadConfiguration`, is a two-leg bull put credit spread suitable
> now; which short and long put strikes are candidates; what are its estimated
> net credit, maximum profit, maximum loss, probability of profit, and
> defined-risk metrics; and what structured recommendation and score should
> downstream decision systems see?

The answer is analytical evidence only. It is not a trade approval, order, risk
reservation, position update, or broker instruction.

### 1.1 Gap filled

| Component | Contractual boundary |
|---|---|
| `strategy/base_strategy.py` | Defines the common plugin contract and invokes strategy logic through `run(StrategyContext)`. |
| `strategy/strategy_evaluation_engine.py` | Invokes this plugin with context, collects reports, and compares strategy reports. |
| This module | Evaluates bull-put-spread suitability and emits an immutable signal and recommendation. |
| `strategy/strategy_scoring_framework.py` | Seals normalized factor inputs into `StrategyScore`, `ConfidenceReport`, and `StrategyExplanation`. |
| Trade Decision Engine | Selects among evaluation reports and independently approves, declines, or defers a possible trade. |
| Risk Engine | Enforces authoritative risk, margin, concentration, event, and portfolio constraints. |
| Execution / Order Manager | Builds, routes, modifies, and cancels approved orders. |

### 1.2 Frozen pipeline

```text
MarketSnapshot (+ optional HistoricalData / PortfolioSnapshot / RiskProfile)
  → BullPutSpreadStrategy (BaseStrategy plugin)
  → TradingSignal + BullPutSpreadRecommendation
      (embeds StrategyScore via scoring framework)
  → Strategy Evaluation Engine
  → Trade Decision Engine
  → Risk
  → Execution / Order Manager
```

The evaluation engine invokes `BaseStrategy.run(StrategyContext)`. This module
is one registered strategy; it never selects itself as “the trade.” The Trade
Decision Engine consumes comparative evaluation reports. Risk, execution, and
broker-order components own their own actions.

### 1.3 Architecture freeze

- **BOUNDARY-BPS-001:** The strategy MUST NOT place an order.
- **BOUNDARY-BPS-002:** The strategy MUST NOT modify or cancel an order.
- **BOUNDARY-BPS-003:** The strategy MUST NOT create, reconcile, or manage a position.
- **BOUNDARY-BPS-004:** The strategy MUST NOT calculate authoritative portfolio risk.
- **BOUNDARY-BPS-005:** The strategy MUST NOT calculate authoritative margin.
- **BOUNDARY-BPS-006:** The strategy MUST NOT calculate position size.
- **BOUNDARY-BPS-007:** The strategy MUST NOT call a broker API.
- **BOUNDARY-BPS-008:** The strategy MUST NOT import or call `kiteconnect`.
- **BOUNDARY-BPS-009:** The strategy MUST NOT fetch a live quote or option chain.
- **BOUNDARY-BPS-010:** The strategy MUST NOT subscribe to a websocket.
- **BOUNDARY-BPS-011:** The strategy MUST NOT load `.env`, files, or credentials.
- **BOUNDARY-BPS-012:** The strategy MUST NOT replace the Evaluation Engine.
- **BOUNDARY-BPS-013:** The strategy MUST NOT replace the Trade Decision Engine.
- **BOUNDARY-BPS-014:** The strategy MUST NOT replace Risk or Execution.
- **BOUNDARY-BPS-015:** The strategy MUST NOT mutate `MarketSnapshot`.
- **BOUNDARY-BPS-016:** The strategy MUST NOT mutate `PortfolioSnapshot`.
- **BOUNDARY-BPS-017:** The strategy MUST NOT mutate context metadata.
- **BOUNDARY-BPS-018:** The strategy MUST NOT retain mutable caller-owned data.
- **BOUNDARY-BPS-019:** The strategy MUST NOT silently infer unavailable Greeks.
- **BOUNDARY-BPS-020:** The strategy MUST NOT represent a heuristic POP as a guarantee.
- **BOUNDARY-BPS-021:** The strategy MUST NOT label bull-put-spread max loss as undefined.
- **BOUNDARY-BPS-022:** The strategy MUST NOT suppress the defined-risk statement.
- **BOUNDARY-BPS-023:** The strategy MUST NOT use wall-clock time except injected context time.
- **BOUNDARY-BPS-024:** The strategy MUST NOT use randomness.
- **BOUNDARY-BPS-025:** The strategy MUST NOT publish a signal with invalid evidence.
- **BOUNDARY-BPS-026:** The strategy MUST NOT invent a missing long put wing to force an entry.
- **BOUNDARY-BPS-027:** The strategy MUST NOT emit a one-leg naked put as a bull put spread.
- **BOUNDARY-BPS-028:** The strategy MUST NOT select call contracts for this structure.

### 1.4 Goals

1. Provide a single deterministic implementation of bull-put-spread suitability.
2. Prefer mildly bullish or bullish regimes with supportive IV.
3. Reject strong bearish trend, crisis, stale, incomplete, and illiquid conditions.
4. Select exactly two put legs: short put and long put further OTM / lower strike.
5. Select strikes by configurable short and long put-delta targets.
6. Calculate expected net credit, max profit, max loss, and POP heuristic.
7. Explain every recommendation and every abstention.
8. Produce immutable artifacts that downstream components can serialize safely.
9. Integrate with the shared scoring framework without reimplementing scoring.
10. Make defined-risk geometry unambiguous to every consumer.
11. Permit deterministic unit tests with no broker or network dependency.
12. Preserve the locked platform pipeline.

### 1.5 Success criteria

- Equivalent valid inputs yield equivalent sealed outputs across runs and threads.
- Each `ENTER` recommendation contains exactly two put legs with correct geometry.
- Each `ABSTAIN` or `REJECT` recommendation contains a stable machine code and reason.
- Candidate ranking is nearest delta, then tighter spread, then higher OI.
- Missing mandatory inputs fail closed before an entry recommendation.
- No production code path imports broker, websocket, credential, or environment facilities.
- Every entry artifact states that max loss is finite and `DEFINED_RISK`.
- Direction on ENTER is `BULLISH`.
- Unit coverage of `strategy/bull_put_spread_strategy.py` is at least 95%.

---

## 2. Responsibilities

| ID | Requirement |
|---|---|
| R1 | Implement the `BaseStrategy` plugin contract. |
| R2 | Expose immutable identity metadata. |
| R3 | Accept only injected context and immutable configuration. |
| R4 | Validate required snapshot identity and observation time. |
| R5 | Validate underlying support. |
| R6 | Validate the entry time window. |
| R7 | Inspect injected regime evidence for bullish suitability. |
| R8 | Inspect injected trend-direction and trend-strength evidence. |
| R9 | Reject strong bearish trend even when IV is attractive. |
| R10 | Inspect injected IV and IV-rank evidence. |
| R11 | Use injected history only when configuration permits fallback derivation. |
| R12 | Validate put-chain completeness for two legs. |
| R13 | Filter expired, malformed, and non-put contracts. |
| R14 | Filter contracts outside liquidity thresholds. |
| R15 | Select a compatible expiry deterministically. |
| R16 | Select the short put by short-delta proximity. |
| R17 | Select the long put further OTM / lower strike than the short put. |
| R18 | Validate bull-put-spread strike geometry. |
| R19 | Calculate configured premium using MID or CONSERVATIVE policy. |
| R20 | Calculate expected net credit. |
| R21 | Calculate maximum profit as net credit × multiplier. |
| R22 | Calculate maximum loss from wing width minus net credit. |
| R23 | Calculate a documented POP heuristic. |
| R24 | Produce scoring-factor inputs with provenance. |
| R25 | Call `StrategyScoringFramework.score()` only after gates pass or with explicit abstention evidence. |
| R26 | Map sealed scoring artifacts to `TradingSignal`. |
| R27 | Include a two-leg put structure hint for entries. |
| R28 | Include stable, ordered explanatory reasons. |
| R29 | Produce an immutable plugin-internal evaluation artifact. |
| R30 | Serialize public models using versioned canonical payloads. |
| R31 | Reject invalid deserialized payloads. |
| R32 | Support optional observational event publication through an injected sink. |
| R33 | Preserve an informational risk-profile hint without enforcing it. |
| R34 | Preserve an informational portfolio snapshot without mutating or pricing it. |
| R35 | Provide deterministic ranking keys for evaluation-engine consumption. |
| R36 | Make all gate outcomes auditable with identifiers and observed values. |
| R37 | Support safe empty-chain abstention. |
| R38 | Keep strategy state stateless and thread-safe. |
| R39 | Produce `StrategyScore`, `ConfidenceReport`, and `StrategyExplanation`. |
| R40 | Emit BULLISH direction on ENTER recommendations. |

---

## 3. Non-responsibilities

| ID | Explicit exclusion |
|---|---|
| NR1 | Broker authentication |
| NR2 | Broker HTTP or websocket transport |
| NR3 | Live or historical market-data retrieval |
| NR4 | Environment-variable loading |
| NR5 | Configuration-file discovery |
| NR6 | Order construction |
| NR7 | Order placement |
| NR8 | Order amendment |
| NR9 | Order cancellation |
| NR10 | Fill reconciliation |
| NR11 | Position lifecycle management |
| NR12 | Exit execution |
| NR13 | Stop-loss enforcement |
| NR14 | Adjustment execution |
| NR15 | Margin calculation |
| NR16 | Buying-power validation |
| NR17 | Portfolio exposure enforcement |
| NR18 | Position sizing |
| NR19 | Trade approval |
| NR20 | Strategy selection |
| NR21 | Regime-model training |
| NR22 | Greeks-model calculation when a required Greek is absent |
| NR23 | IV-surface construction |
| NR24 | Profitability guarantee |
| NR25 | Backtest orchestration |
| NR26 | Persistence of recommendations |
| NR27 | Scheduling or polling |
| NR28 | Event-calendar acquisition |
| NR29 | Mutation of input snapshots |
| NR30 | Replacement of any frozen pipeline component |
| NR31 | Authoritative portfolio risk calculation |
| NR32 | Conversion of defined-risk geometry into broker multi-leg orders |
| NR33 | Selection of call wings or iron-condor structures |

---

## 4. Strategy identity and registry metadata

The registration key is exactly `bull_put_spread`. It is lowercase, stable, and
not user-configurable.

| Metadata field | Required value |
|---|---|
| `strategy_id` | `bull_put_spread` |
| `display_name` | `Bull Put Spread` |
| `family` | `bull_put_spread` |
| `version` | `1.0.0` |
| `direction` | `BULLISH` |
| `risk_profile_hint` | `DEFINED` / `DEFINED_RISK` |
| `required_structure` | Two put legs (short put, long put) |
| `scoring_profile_default` | `PREMIUM_SELLING` |
| `supports_direct_execution` | `false` |
| `supports_position_management` | `false` |

**REG-BPS-001:** Registry registration MUST use key `bull_put_spread`.

**REG-BPS-002:** Duplicate registration MUST fail at registry construction.

**REG-BPS-003:** The registration factory MUST receive immutable configuration
and optional injected collaborators only.

**REG-BPS-004:** Registry metadata MUST advertise defined / finite risk.

**REG-BPS-005:** A registry consumer MUST NOT infer that registration authorizes
trading.

**REG-BPS-006:** The family enum value MUST be `StrategyFamily.BULL_PUT_SPREAD`.

---

## 5. Suitability gates

All gates are fail closed. A gate outcome is `PASS`, `ABSTAIN`, or `REJECT`.
`REJECT` denotes malformed, inconsistent, unsupported, or policy-prohibited
input. `ABSTAIN` denotes valid input that is unsuitable now. Neither outcome
creates an entry structure.

### 5.1 Gate ordering

1. Context and configuration validation.
2. Snapshot freshness and identity.
3. Underlying and time window.
4. Regime and event evidence.
5. Trend-direction and trend-strength suitability.
6. Strong-bearish rejection.
7. IV and IV-rank suitability.
8. Put-chain completeness.
9. Liquidity.
10. Expiry and two-leg strike selection.
11. Geometry, premium, and metric validation.
12. Scoring and signal sealing.

### 5.2 Regime gate

| Regime tag | Default outcome | Rule |
|---|---|---|
| `TRENDING_UP` | PASS | Preferred when other gates pass. |
| `BULLISH` | PASS | Explicit bullish suitability. |
| `MEAN_REVERTING` | PASS | Permitted when trend evidence is not strongly bearish. |
| `RANGE_BOUND` | PASS | Permitted when bullish bias or non-bearish trend evidence is present. |
| `SIDEWAYS` | ABSTAIN | Insufficient directional evidence by default. |
| `NEUTRAL` | ABSTAIN | Insufficient directional evidence by default. |
| `TRENDING_DOWN` | ABSTAIN | Bearish trend is unsuitable for bull put credit. |
| `BEARISH` | ABSTAIN | Bearish regime is unsuitable. |
| `BREAKOUT` | ABSTAIN | Expansion risk is unsuitable by default. |
| `HIGH_VOLATILITY_CRISIS` | REJECT | Crisis condition violates entry policy. |
| absent | REJECT | Required regime evidence is absent. |

- **GATE-BPS-001:** Only explicit regime tags may be used.
- **GATE-BPS-002:** A regime score cannot overturn an unsuitable regime tag.
- **GATE-BPS-003:** A crisis tag is an immediate reject.
- **GATE-BPS-004:** A contradictory set of supplied tags is a reject.
- **GATE-BPS-005:** Regime evidence must identify its observation timestamp.
- **GATE-BPS-006:** The strategy MUST NOT invent a bullish regime from price action.

### 5.3 Trend-direction and strong-bearish gate

Bull put spreads are damaged by downside expansion. Strong bearish evidence
abstains even if IV looks attractive.

| Trend evidence | Default outcome |
|---|---|
| Missing when `require_trend_evidence` is true | REJECT |
| Direction tag `BEARISH` or `TRENDING_DOWN` | ABSTAIN (`BPS.TREND.STRONG_BEARISH`) |
| Direction tag `BULLISH` or `TRENDING_UP` | PASS when strength within bounds |
| Strength missing when `require_trend_strength` is true | REJECT |
| Strength non-finite | REJECT |
| Bearish strength `>= maximum_bearish_trend_strength` | ABSTAIN |
| Bullish/neutral strength within configured bounds | PASS |

- **GATE-BPS-007:** Strong bearish trend MUST abstain.
- **GATE-BPS-008:** Trend evidence is informational suitability only; it is not a risk limit.
- **GATE-BPS-009:** Trend evidence MUST be injected; it is not fetched.
- **GATE-BPS-010:** When only a scalar `trend_strength` is supplied without direction,
  values at or above `maximum_bearish_trend_strength` are treated as strong-bearish
  abstention under the documented tag contract.

### 5.4 IV and IV-rank gate

`iv_rank` is a supplied bounded percentile in `[0, 100]`. It is not inferred
from a broker call. If injected historical series is used to calculate an
allowed fallback rank, the complete series must already be in the context.

- **GATE-BPS-010:** `iv_rank >= minimum_iv_rank` is mandatory.
- **GATE-BPS-011:** Non-finite IV or IV rank is rejected.
- **GATE-BPS-012:** Missing IV rank is rejected when `require_iv_rank` is true.
- **GATE-BPS-013:** A fallback rank may be used only when configuration enables it.
- **GATE-BPS-014:** A fallback requires at least `iv_rank_lookback_observations`.
- **GATE-BPS-015:** IV rank is a suitability signal, never a profitability guarantee.
- **GATE-BPS-016:** Unsuitable / low IV relative to `minimum_iv_rank` abstains
  (`BPS.IV_RANK.LOW` / unsuitable volatility).

### 5.5 Liquidity gate

Every selected leg must independently pass liquidity. The structure passes only
when both put legs pass.

| Metric | Default interpretation |
|---|---|
| Bid | Must be finite and non-negative. |
| Ask | Must be finite, positive, and at least bid. |
| Absolute spread | `ask - bid <= maximum_spread_width`. |
| Relative spread | `(ask - bid) / midpoint <= maximum_relative_spread_width`. |
| Open interest | `oi >= minimum_open_interest`. |
| Volume | `volume >= minimum_volume`. |
| Quote time | Within configured quote-age threshold if available. |

- **GATE-BPS-020:** Missing bid or ask rejects the affected contract.
- **GATE-BPS-021:** Crossed quotes reject the affected contract.
- **GATE-BPS-022:** Zero or negative midpoint rejects the affected contract.
- **GATE-BPS-023:** OI below the floor abstains for that candidate.
- **GATE-BPS-024:** Volume below the floor abstains for that candidate.
- **GATE-BPS-025:** Spread above either enabled limit abstains for that candidate.
- **GATE-BPS-026:** An absent optional OI field rejects when OI is required.
- **GATE-BPS-027:** An absent optional volume field rejects when volume is required.
- **GATE-BPS-028:** Poor liquidity on either selected leg abstains the structure.

### 5.6 Time-window gate

The configuration contains explicit exchange-local entry and informational exit
windows. Context supplies the observed timestamp and exchange timezone.

- **GATE-BPS-030:** Entry is permitted only inside an inclusive start and exclusive end interval.
- **GATE-BPS-031:** The exit window is copied to metadata and is never acted on.
- **GATE-BPS-032:** Missing timezone data rejects a time-window evaluation.
- **GATE-BPS-033:** A timestamp on the end boundary abstains.
- **GATE-BPS-034:** Cross-midnight windows are rejected in v1.0.
- **GATE-BPS-035:** The plugin never waits for a future window.

### 5.7 Chain-completeness gate

- **GATE-BPS-040:** Underlying spot must be finite and strictly positive.
- **GATE-BPS-041:** At least one eligible short-put candidate must exist.
- **GATE-BPS-042:** At least one eligible long-put candidate below the short put must exist.
- **GATE-BPS-043:** Both legs must share a selected expiry.
- **GATE-BPS-044:** Required Greek fields must be present for every selected leg.
- **GATE-BPS-045:** Contract strike, expiry, type, and quote identity must agree.
- **GATE-BPS-046:** Duplicate instrument identifiers with conflicting facts reject the snapshot.
- **GATE-BPS-047:** Insufficient put chain for two distinct instruments rejects or abstains
  with `BPS.CHAIN.INCOMPLETE`.
- **GATE-BPS-048:** Call-only chains are insufficient.

### 5.8 Snapshot and context rejects

| Condition | Code | State |
|---|---|---|
| Missing market snapshot | `BPS.SNAPSHOT.MISSING` | REJECT |
| Stale snapshot | `BPS.SNAPSHOT.STALE` | REJECT |
| Unsupported underlying | `BPS.UNDERLYING.UNSUPPORTED` | REJECT |
| Outside entry window | `BPS.TIME.OUTSIDE_ENTRY_WINDOW` | ABSTAIN |
| Regime missing | `BPS.REGIME.MISSING` | REJECT |
| Regime crisis | `BPS.REGIME.CRISIS` | REJECT |
| Regime unsuitable | `BPS.REGIME.UNSUITABLE` | ABSTAIN |
| Strong bearish trend | `BPS.TREND.STRONG_BEARISH` | ABSTAIN |
| Adverse event | `BPS.EVENT.ADVERSE` | ABSTAIN |
| IV rank missing | `BPS.IV_RANK.MISSING` | REJECT |
| IV rank low / unsuitable vol | `BPS.IV_RANK.LOW` | ABSTAIN |
| Poor liquidity | `BPS.LIQUIDITY.POOR` | ABSTAIN |
| Incomplete chain | `BPS.CHAIN.INCOMPLETE` | REJECT |
| Missing Greeks | `BPS.GREEKS.MISSING` | REJECT |
| Invalid geometry | `BPS.STRUCTURE.INVALID_GEOMETRY` | REJECT |
| Credit below floor | `BPS.PREMIUM.BELOW_MINIMUM` | ABSTAIN |
| Non-positive max loss | `BPS.RISK.NON_POSITIVE_MAX_LOSS` | REJECT |

---

## 6. Strike selection algorithm

The algorithm selects two put legs for a single expiry:

1. Short put (SELL PE) — higher strike
2. Long put (BUY PE) — lower strike / further OTM

It is deterministic and never calls a broker or market-data service.

### 6.1 Definitions

| Term | Definition |
|---|---|
| OTM put | Contract with `strike < spot` when `require_short_otm` is true. |
| Short-put target | `abs(config.short_target_delta)` or `short_put_target_delta`. |
| Long-put target | `abs(config.long_target_delta)` or `long_put_target_delta`. |
| Delta error | `abs(abs(contract.delta) - target_delta)`. |
| Wing width | `short_put_strike - long_put_strike` (strictly positive). |
| Eligible short put | PE, selected expiry, valid quote, liquidity-pass, valid required Greek, within short-delta tolerance, OTM when required. |
| Eligible long put | PE, selected expiry, `strike < short_put_strike`, valid quote, liquidity-pass, valid required Greek, within long-delta or wing-width policy. |

### 6.2 Expiry selection

1. Group valid put contracts by expiry.
2. Exclude expiries earlier than the context observation date.
3. Exclude expiries outside configured DTE bounds.
4. Retain expiries containing short and long put candidates.
5. Choose the expiry with the lowest non-negative DTE.
6. If DTE ties, choose the earlier normalized expiry timestamp.
7. If normalized expiry ties, choose lexicographically smallest expiry identifier.

- **STRIKE-BPS-001:** Expiry selection is completed before leg selection.
- **STRIKE-BPS-002:** A same-day expiry is permitted only if `minimum_dte == 0`.
- **STRIKE-BPS-003:** Expired contracts are never candidates.
- **STRIKE-BPS-004:** Both legs MUST use the same expiry.

### 6.3 Short-put candidate ranking

Sort eligible short-put candidates by this ascending tuple:

```text
(
  abs(abs(delta) - short_target_delta),
  relative_spread,
  -open_interest,
  -volume,
  strike,
  instrument_id,
)
```

- **STRIKE-BPS-010:** Short delta error must not exceed `short_delta_selection_tolerance`.
- **STRIKE-BPS-011:** When `require_short_otm` is true, short PE must satisfy `strike < spot`.
- **STRIKE-BPS-012:** Equal ranking tuples are resolved by `instrument_id`.
- **STRIKE-BPS-013:** Floating values are compared as normalized decimals.
- **STRIKE-BPS-014:** The input option-chain order must not influence selection.
- **STRIKE-BPS-015:** Call contracts are never short-put candidates.

### 6.4 Long-put / wing selection

After the short put is fixed, the long put is selected under one configured policy:

| Policy | Behavior |
|---|---|
| `DELTA_TARGET` | Rank lower-strike candidates by long-delta proximity. |
| `FIXED_WIDTH` | Prefer contracts whose strike distance equals `target_wing_width`. |
| `WIDTH_THEN_DELTA` | Filter by wing-width bounds, then rank by long-delta proximity. |

Long-put candidates must satisfy `strike < short_put_strike`.

Long-leg ranking tuple:

```text
(
  abs(abs(delta) - long_target_delta),
  abs(wing_width - target_wing_width) if target_wing_width else 0,
  relative_spread,
  -open_interest,
  -volume,
  strike,
  instrument_id,
)
```

- **STRIKE-BPS-020:** Long PE strike MUST be below short PE strike.
- **STRIKE-BPS-021:** Wing width MUST be strictly positive.
- **STRIKE-BPS-022:** Wing width MUST satisfy configured min/max bounds when set.
- **STRIKE-BPS-023:** Long delta magnitude MUST be strictly less than short delta magnitude when both Greeks are present.
- **STRIKE-BPS-024:** Long put must remain a put (`OptionType.PE`).

### 6.5 Pseudocode

```python
def choose_short_put(
    contracts: tuple[OptionContract, ...],
    spot: Decimal,
    expiry: date,
    target_delta: Decimal,
    config: BullPutSpreadConfiguration,
) -> OptionContract | GateFailure:
    candidates = [
        contract
        for contract in contracts
        if is_eligible_short_put(contract, spot, expiry, config)
        and abs(abs(contract.delta) - target_delta)
        <= config.short_delta_selection_tolerance
    ]
    if not candidates:
        return GateFailure("BPS.STRIKE.NO_ELIGIBLE_SHORT")
    return min(candidates, key=short_candidate_rank_key)


def choose_long_put(
    contracts: tuple[OptionContract, ...],
    spot: Decimal,
    expiry: date,
    short_put: OptionContract,
    target_delta: Decimal,
    config: BullPutSpreadConfiguration,
) -> OptionContract | GateFailure:
    candidates = [
        contract
        for contract in contracts
        if is_eligible_long_put(contract, spot, expiry, short_put, config)
    ]
    if not candidates:
        return GateFailure("BPS.STRIKE.NO_ELIGIBLE_LONG")
    return min(candidates, key=long_candidate_rank_key)


def select_bull_put_spread(...):
    expiry = choose_expiry(...)
    short_put = choose_short_put(..., short_target)
    long_put = choose_long_put(..., short_put, long_target)
    validate_geometry(spot, short_put, long_put)
    return BullPutSpreadStrikeSelection(...)
```

### 6.6 Structure geometry validation

Required ordering after selection:

```text
long_put_strike < short_put_strike; spot relationship per require_short_otm
```

Canonical preferred credit geometry:

```text
long_put_strike < short_put_strike < spot
```

- **STRIKE-BPS-030:** Selected contracts MUST have two distinct instrument IDs.
- **STRIKE-BPS-031:** Selected contracts MUST have the same underlying.
- **STRIKE-BPS-032:** Selected contracts MUST have the selected expiry.
- **STRIKE-BPS-033:** Selected contract deltas MUST be put deltas (non-positive expected).
- **STRIKE-BPS-034:** Recommendation leg sides are `SELL, BUY` as a structure hint only.
- **STRIKE-BPS-035:** Canonical leg index order for structure hints MUST be:

| Index | Role | Side | Option type |
|---|---|---|---|
| 0 | Short put | SELL | PE |
| 1 | Long put | BUY | PE |

This ordering is compatible with `execution/execution_engine.py` family-side
resolution for `StrategyFamily.BULL_PUT_SPREAD`.

---

## 7. Premium, POP, and risk metrics

All calculations use `Decimal` internally and are rounded only when sealing
public outputs. Monetary values are expressed in snapshot currency units per
underlying unit unless a multiplier is explicitly supplied.

### 7.1 Price policy

| Policy | Short-leg credit price | Long-leg debit price | Use |
|---|---|---|---|
| `MID` | `(bid + ask) / 2` | `(bid + ask) / 2` | Neutral estimate. |
| `CONSERVATIVE` | `bid` for short | `ask` for long | Conservative net-credit estimate. |
| `ASK_CREDIT` | `ask` for short | `bid` for long | Optimistic estimate; allowed only when explicitly configured. |

**METRIC-BPS-001:** A policy is applied consistently across both legs.

**METRIC-BPS-002:** If a required quote is unavailable, the strategy abstains.

**METRIC-BPS-003:** The policy estimate is not an executable fill prediction.

**METRIC-BPS-004:** Default v1.0 policy is `MID`.

### 7.2 Credit, maximum profit, and maximum loss

```text
short_put_credit = price_short(selected_short_put, policy)
long_put_debit   = price_long(selected_long_put, policy)
net_credit       = short_put_credit - long_put_debit
wing_width       = short_put_strike - long_put_strike

max_profit = net_credit × contract_multiplier
max_loss   = (wing_width - net_credit) × contract_multiplier
```

For a standard credit bull put spread, maximum profit is the received net
credit when the short put expires worthless. Maximum loss is the wing width
minus net credit (times multiplier). Fees, taxes, slippage, assignment, and
execution costs are excluded unless already represented by injected facts.

- **METRIC-BPS-010:** `net_credit` MUST be strictly positive to ENTER.
- **METRIC-BPS-011:** `net_credit >= minimum_premium` is mandatory for ENTER.
- **METRIC-BPS-012:** `max_loss` MUST be finite and strictly positive.
- **METRIC-BPS-013:** If `wing_width <= net_credit`, reject with
  `BPS.RISK.NON_POSITIVE_MAX_LOSS`.
- **METRIC-BPS-014:** The strategy MUST label max loss as `DEFINED_RISK`.
- **METRIC-BPS-015:** The strategy MUST NOT substitute margin for max loss.
- **METRIC-BPS-016:** The strategy MUST NOT invent an infinite loss.

### 7.3 Probability-of-profit heuristic

The v1.0 POP is a transparent ranking heuristic:

```text
short_otm_probability = clamp(1 - abs(short_put_delta), 0, 1)
credit_adjustment     = min(net_credit / max(spot, epsilon), 0.05)
defined_risk_adjustment = min(net_credit / max(max_loss / multiplier, epsilon), 0.05)
pop = clamp(
    short_otm_probability + 0.5 * credit_adjustment + 0.5 * defined_risk_adjustment,
    0,
    1,
)
```

It is not a pricing model, distribution model, backtest, guarantee, or risk
limit. `epsilon` is an internal positive decimal used only after spot and
max-loss positivity validation.

### 7.4 Risk statement

| Metric | Required v1.0 value |
|---|---|
| `max_profit` | Estimated net credit multiplied by multiplier. |
| `max_loss` | Finite `(wing_width - net_credit) × multiplier`. |
| `max_loss_label` | `DEFINED_RISK`. |
| `risk_profile_hint` | `DEFINED`. |
| `capital_at_risk` | Informational copy of `max_loss`; Risk Engine owns authoritative calculation. |
| `margin_required` | `None`; Risk Engine / broker owns authoritative calculation. |
| `lower_breakeven` | Informational: `short_put_strike - net_credit`. |
| `reward_risk_ratio` | Informational: `max_profit / max_loss` when `max_loss > 0`. |

**METRIC-BPS-020:** Every entry explanation MUST include the defined-risk statement.

**METRIC-BPS-021:** Every entry artifact MUST expose wing width.

**METRIC-BPS-022:** Breakevens are informational only and never become stops.

---

## 8. Scoring integration

The strategy extracts facts and calls `StrategyScoringFramework.score()` with a
`FactorInputBundle`. The framework owns normalization, weighting, confidence
math, explanation sealing, and score serialization.

### 8.1 PREMIUM_SELLING factor map

| Factor category | Source | Strategy mapping |
|---|---|---|
| `MARKET_REGIME` | Injected regime tag and score | Bullish / mildly-bullish suitability. |
| `TREND_ALIGNMENT` | Injected trend direction and strength | Rewards bullish alignment; penalizes strong bearish trend. |
| `VOLATILITY` | IV rank and IV evidence | Rewards elevated IV above floor for credit entry. |
| `LIQUIDITY` | Selected-leg quote/OI/volume facts | Rewards tight, liquid selected put legs. |
| `GREEKS` | Selected short and long put deltas | Rewards target proximity and ordered magnitudes. |
| `RISK_REWARD` | Credit, POP heuristic, defined max loss | Score is suitability only; never conceals loss geometry. |
| `EVENT_RISK` | Injected event flags | Penalizes known elevated event risk. |

- **SCORE-BPS-001:** Factor provenance MUST identify snapshot or injected metadata origin.
- **SCORE-BPS-002:** No factor may be fabricated to fill missing mandatory evidence.
- **SCORE-BPS-003:** The score profile defaults to `PREMIUM_SELLING`.
- **SCORE-BPS-004:** Unknown profile names reject configuration.
- **SCORE-BPS-005:** A sealed score does not authorize an entry.
- **SCORE-BPS-006:** Defined-risk reward/risk facts MUST appear in explanation text.

### 8.2 Confidence mapping

The strategy forwards the framework-produced `ConfidenceReport` unchanged.
`SignalConfidence` is mapped from its band using the common project mapping.
An abstention may have high confidence: high confidence can mean strong
evidence that conditions are unsuitable.

### 8.3 Explanation requirements

Every sealed `StrategyExplanation` for an ENTER recommendation MUST include:

1. Selected expiry and two put strikes.
2. Net credit, max profit, and max loss.
3. Defined-risk label.
4. Regime, trend, and IV-rank evidence identifiers.
5. Stable reason codes in gate order.

---

## 9. TradingSignal mapping

| Recommendation state | TradingSignal action | Structure hint | Meaning |
|---|---|---|---|
| `ENTER` | `ENTER` or project-equivalent evaluate/entry action | Two put legs | Suitable analytical candidate; downstream approval required. |
| `ABSTAIN` | `ABSTAIN` | None | Valid context, insufficient suitability now. |
| `REJECT` | `REJECT` | None | Invalid, stale, unsupported, or prohibited input. |

For `ENTER`, direction is `BULLISH`; the structure hint contains two PE legs in
canonical index order with exact selected contract identity. It is a declarative
recommendation, never an order request.

- **SIGNAL-BPS-001:** Signal reasons are stable and ordered by gate sequence.
- **SIGNAL-BPS-002:** `ENTER` includes score, confidence, explanation, and recommendation ID.
- **SIGNAL-BPS-003:** `ABSTAIN` includes all successful gate observations before the first failure.
- **SIGNAL-BPS-004:** `REJECT` includes a stable error code and safe details.
- **SIGNAL-BPS-005:** A signal does not expose credentials, portfolio account identifiers, or raw secrets.
- **SIGNAL-BPS-006:** `ENTER` risk metadata uses `RiskProfileHint.DEFINED` and
  `max_loss_category="DEFINED_RISK"`.
- **SIGNAL-BPS-007:** Structure type string is `bull_put_spread` (or project-compatible `vertical` alias only in metadata, never as the primary family).
- **SIGNAL-BPS-008:** Direction MUST be `BULLISH` on ENTER.

---

## 10. Configuration

`BullPutSpreadConfiguration` is a frozen dataclass. All values are validated at
construction; an invalid configuration cannot be used to evaluate a snapshot.

| Field | Type | Default | Validation |
|---|---|---|---|
| `short_target_delta` | `Decimal` | `0.25` | `(0, 0.50)` |
| `long_target_delta` | `Decimal` | `0.10` | `(0, short_target_delta)` |
| `short_put_target_delta` | `Decimal \| None` | `None` | Uses shared short target when absent. |
| `long_put_target_delta` | `Decimal \| None` | `None` | Uses shared long target when absent. |
| `wing_selection_policy` | `WingSelectionPolicy` | `WIDTH_THEN_DELTA` | Known enum. |
| `target_wing_width` | `Decimal \| None` | `None` | Positive when set. |
| `minimum_wing_width` | `Decimal \| None` | `None` | Positive when set. |
| `maximum_wing_width` | `Decimal \| None` | `None` | At least minimum when both set. |
| `require_short_otm` | `bool` | `True` | Boolean. |
| `minimum_iv_rank` | `Decimal` | `40` | `[0, 100]` |
| `maximum_bearish_trend_strength` | `Decimal` | `0.55` | `[0, 1]` |
| `require_trend_strength` | `bool` | `True` | Boolean. |
| `require_trend_evidence` | `bool` | `True` | Boolean. |
| `maximum_spread_width` | `Decimal \| None` | `None` | Positive when set. |
| `maximum_relative_spread_width` | `Decimal` | `0.15` | `(0, 1]` |
| `minimum_premium` | `Decimal` | `0` | Non-negative. |
| `minimum_open_interest` | `int` | `1` | Non-negative. |
| `minimum_volume` | `int` | `1` | Non-negative. |
| `minimum_liquidity_score` | `Decimal \| None` | `None` | `[0, 1]` when set. |
| `entry_time_window` | `TimeWindow` | exchange config | Valid same-day interval. |
| `exit_time_window` | `TimeWindow` | exchange config | Informational only. |
| `scoring_profile_name` | `str` | `PREMIUM_SELLING` | Known profile. |
| `supported_underlyings` | `frozenset[str]` | NIFTY/BANKNIFTY/SENSEX | Non-empty normalized values. |
| `max_snapshot_age_seconds` | `int` | `5` | Positive. |
| `require_valid_snapshot` | `bool` | `True` | Boolean. |
| `short_delta_selection_tolerance` | `Decimal` | `0.03` | `[0, 0.50)`. |
| `long_delta_selection_tolerance` | `Decimal` | `0.03` | `[0, 0.50)`. |
| `premium_price_policy` | `PremiumPricePolicy` | `MID` | Known enum. |
| `minimum_dte` | `int` | `0` | Non-negative. |
| `maximum_dte` | `int` | `45` | At least minimum DTE. |
| `require_iv_rank` | `bool` | `True` | Boolean. |
| `require_greeks` | `bool` | `True` | Boolean. |
| `require_open_interest` | `bool` | `True` | Boolean. |
| `require_volume` | `bool` | `True` | Boolean. |
| `iv_rank_lookback_observations` | `int` | `252` | Positive. |
| `contract_multiplier` | `Decimal` | `1` | Positive. |

### 10.1 Configuration invariants

- **CFG-BPS-001:** Decimal fields must be finite.
- **CFG-BPS-002:** A short-put target overrides only the short-put target.
- **CFG-BPS-003:** A long-put target overrides only the long-put target.
- **CFG-BPS-004:** Short target magnitudes must be strictly less than 0.50.
- **CFG-BPS-005:** Long target magnitudes must be strictly less than the short target.
- **CFG-BPS-006:** `minimum_premium` is evaluated on total net credit.
- **CFG-BPS-007:** An empty underlying set is invalid.
- **CFG-BPS-008:** Underlying strings are normalized to uppercase at construction.
- **CFG-BPS-009:** `maximum_dte < minimum_dte` is invalid.
- **CFG-BPS-010:** A `None` absolute spread limit disables only that limit.
- **CFG-BPS-011:** Exit-window configuration never becomes exit behavior.
- **CFG-BPS-012:** `long_target_delta >= short_target_delta` is invalid.
- **CFG-BPS-013:** `maximum_bearish_trend_strength` must be finite and in `[0, 1]`.
- **CFG-BPS-014:** Only `PREMIUM_SELLING` is supported in v1.0.

---

## 11. Frozen public models

### 11.1 `BullPutSpreadStrikeSelection`

| Field | Type | Meaning |
|---|---|---|
| `underlying` | `str` | Normalized underlying identity. |
| `spot` | `Decimal` | Evaluation spot. |
| `expiry` | `date` | Shared selected expiry. |
| `short_put_strike` | `Decimal` | Selected short PE strike. |
| `long_put_strike` | `Decimal` | Selected long PE strike. |
| `short_put_instrument_id` | `str` | Immutable instrument identity. |
| `long_put_instrument_id` | `str` | Immutable instrument identity. |
| `short_put_delta` | `Decimal` | Observed short delta. |
| `long_put_delta` | `Decimal` | Observed long delta. |
| `wing_width` | `Decimal` | `short_put - long_put`. |
| `dte` | `int` | Days to expiry at evaluation date. |

### 11.2 `BullPutSpreadRiskMetrics`

| Field | Type | Meaning |
|---|---|---|
| `net_credit` | `Decimal` | Estimated structure credit. |
| `max_profit` | `Decimal` | `net_credit × multiplier`. |
| `max_loss` | `Decimal` | Finite defined loss. |
| `max_loss_label` | `str` | Always `DEFINED_RISK`. |
| `probability_of_profit` | `Decimal` | Heuristic in `[0, 1]`. |
| `reward_risk_ratio` | `Decimal` | `max_profit / max_loss`. |
| `lower_breakeven` | `Decimal \| None` | Informational. |
| `short_put_credit` | `Decimal` | Short-leg credit. |
| `long_put_debit` | `Decimal` | Long-leg debit. |
| `contract_multiplier` | `Decimal` | Applied multiplier. |

### 11.3 `BullPutSpreadRecommendation`

| Field | Type | Meaning |
|---|---|---|
| `recommendation_id` | `str` | Stable deterministic identifier. |
| `state` | `EntryRecommendationState` | `ENTER`, `ABSTAIN`, or `REJECT`. |
| `strategy_id` | `str` | Always `bull_put_spread`. |
| `as_of` | `datetime` | Evaluation timestamp from context. |
| `strike_selection` | `BullPutSpreadStrikeSelection \| None` | Present on ENTER. |
| `risk_metrics` | `BullPutSpreadRiskMetrics \| None` | Present on ENTER. |
| `strategy_score` | `StrategyScore \| None` | Sealed score when available. |
| `confidence` | `ConfidenceReport \| None` | Sealed confidence when available. |
| `explanation` | `StrategyExplanation \| None` | Sealed explanation when available. |
| `reasons` | `tuple[str, ...]` | Ordered machine-readable reason codes. |
| `schema_version` | `str` | Serialization schema version. |

### 11.4 `BullPutSpreadEvaluationResult`

| Field | Type | Meaning |
|---|---|---|
| `recommendation` | `BullPutSpreadRecommendation` | Immutable recommendation artifact. |
| `signal` | `TradingSignal` | Framework-compatible signal. |

### 11.5 Supporting models

| Model | Role |
|---|---|
| `MarketRegimeEvidence` | Injected regime tag/score/as-of. |
| `TrendEvidence` | Injected trend direction and/or strength. |
| `EventRiskEvidence` | Injected adverse-event flag. |
| `TimeWindow` | Exchange-local start/end interval. |
| `BullPutSpreadContext` | Optional typed wrapper over `StrategyContext` facts. |
| `PremiumPricePolicy` | Quote policy enum. |
| `WingSelectionPolicy` | Long-leg selection policy enum. |
| `EntryRecommendationState` | `ENTER` / `ABSTAIN` / `REJECT`. |

All public models are immutable (`frozen=True`) dataclasses.

---

## 12. Public API

```python
class BullPutSpreadStrategy(BaseStrategy):
    def __init__(
        self,
        configuration: BullPutSpreadConfiguration,
        scoring_framework: StrategyScoringFramework,
        *,
        plugin_config: StrategyPluginConfig | None = None,
        event_sink: object | None = None,
    ) -> None: ...

    def evaluate(self, context: object) -> object: ...
    def evaluate_recommendation(
        self, context: StrategyContext | BullPutSpreadContext
    ) -> BullPutSpreadRecommendation: ...
    def evaluate_bull_put_spread(
        self, context: StrategyContext | BullPutSpreadContext
    ) -> BullPutSpreadRecommendation: ...
    def _execute(self, context: StrategyContext) -> TradingSignal: ...


def default_bull_put_spread_configuration() -> BullPutSpreadConfiguration: ...
def to_json(recommendation: BullPutSpreadRecommendation) -> str: ...
def from_json(payload: str) -> BullPutSpreadRecommendation: ...
```

### 12.1 StrategyContext extensions

Evidence may be supplied through:

1. Frozen `BullPutSpreadContext` wrapping `StrategyContext` plus typed evidence.
2. `StrategyContext.tags` string map with documented keys such as:
   - `regime_tag`
   - `iv_rank`
   - `trend_direction`
   - `trend_strength`
   - `event_adverse`

Tags remain `Mapping[str, str]`. Numeric evidence parsed from tags MUST use
deterministic decimal parsing and fail closed on malformed values.

### 12.2 Dispatch rules

- `StrategyContext` / `BullPutSpreadContext` → recommendation path.
- Generic `EngineContext` → `BaseStrategy.evaluate` behavior unchanged.
- Inherited `run(StrategyContext)` → `_execute` → `TradingSignal`.

---

## 13. Validation

Validation is fail closed and ordered.

| Stage | Examples |
|---|---|
| Configuration | Targets, wing bounds, DTE, IV floors, bearish-trend ceiling. |
| Context | Snapshot presence, as-of timezone awareness. |
| Freshness | Snapshot age versus `max_snapshot_age_seconds`. |
| Underlying | Membership in `supported_underlyings`. |
| Time | Entry window inclusion. |
| Regime | Bullish suitability; crisis reject. |
| Trend | Strong-bearish abstention. |
| IV | Minimum IV rank / unsuitable volatility. |
| Chain | Two-role put candidate existence. |
| Liquidity | Per-leg quote, OI, volume, spread. |
| Strikes | Geometry and distinct instruments. |
| Metrics | Positive credit, positive defined max loss. |
| Scoring | Framework acceptance of factor bundle. |

Reject missing market snapshot, stale data, poor liquidity, unsuitable
volatility, strong bearish trend, and insufficient option chain exactly as
required by this contract.

---

## 14. Determinism and thread safety

- No wall-clock reads.
- No randomness.
- No shared mutable buffers.
- Configuration and collaborators are immutable after construction.
- Concurrent evaluations on shared strategy instances MUST produce isolated
  immutable results.
- Ranking MUST NOT depend on input collection iteration order except through
  documented tie-break keys.
- Decimal normalization precedes comparisons and sealing.

### 14.1 Concurrency sketch

```text
Thread A: evaluate(context_a) → recommendation_a / signal_a
Thread B: evaluate(context_b) → recommendation_b / signal_b
```

No locks are required around evaluation if all inputs and outputs are
immutable and the strategy retains no mutable per-call state. Optional event
sinks MUST isolate their failures from recommendation sealing.

---

## 15. Serialization

Public recommendations use versioned canonical JSON.

| Rule | Requirement |
|---|---|
| Schema version field | Present and equal to module schema constant. |
| Key order | Canonical sorted object keys for sealed payloads. |
| Decimals | String-encoded normalized decimals. |
| Dates | ISO-8601 calendar dates. |
| Datetimes | Timezone-aware ISO-8601. |
| Unknown fields | Reject or ignore per explicit reader policy; v1.0 rejects unknown required-section fields. |
| Incompatible version | Reject with `BPS.SERIALIZATION.UNSUPPORTED_VERSION`. |
| Round trip | `from_json(to_json(x))` preserves semantic equality. |

Serialization MUST NEVER embed credentials, account numbers, or broker tokens.

---

## 16. Error catalog

| Code | Meaning |
|---|---|
| `BPS.CONFIG.INVALID` | Configuration invariant failed. |
| `BPS.CONTEXT.INVALID` | Context malformed. |
| `BPS.SNAPSHOT.MISSING` | Market snapshot absent. |
| `BPS.SNAPSHOT.STALE` | Snapshot older than allowed age. |
| `BPS.UNDERLYING.UNSUPPORTED` | Underlying not allowed. |
| `BPS.TIME.OUTSIDE_ENTRY_WINDOW` | Outside configured entry window. |
| `BPS.REGIME.MISSING` | Regime evidence absent. |
| `BPS.REGIME.CRISIS` | Crisis regime. |
| `BPS.REGIME.UNSUITABLE` | Non-bullish / unsuitable regime. |
| `BPS.TREND.MISSING` | Trend evidence required but absent. |
| `BPS.TREND.STRONG_BEARISH` | Strong bearish trend / unsuitable downside pressure. |
| `BPS.EVENT.ADVERSE` | Adverse event evidence. |
| `BPS.IV_RANK.MISSING` | IV rank required but absent. |
| `BPS.IV_RANK.LOW` | IV rank below floor / unsuitable volatility. |
| `BPS.METRIC.NON_FINITE` | Non-finite suitability metric. |
| `BPS.CHAIN.INCOMPLETE` | Insufficient two-leg put candidates. |
| `BPS.GREEKS.MISSING` | Required Greeks absent. |
| `BPS.LIQUIDITY.POOR` | Selected or candidate liquidity failed. |
| `BPS.STRIKE.NO_ELIGIBLE_SHORT` | No short-put candidate. |
| `BPS.STRIKE.NO_ELIGIBLE_LONG` | No long-put / wing candidate. |
| `BPS.STRUCTURE.INVALID_GEOMETRY` | Strike ordering invalid. |
| `BPS.PREMIUM.BELOW_MINIMUM` | Net credit below configured floor. |
| `BPS.RISK.NON_POSITIVE_MAX_LOSS` | Defined max loss not strictly positive. |
| `BPS.SCORING.FAILED` | Scoring framework rejected inputs. |
| `BPS.SERIALIZATION.UNSUPPORTED_VERSION` | Payload schema unsupported. |
| `BPS.SERIALIZATION.INVALID` | Payload malformed. |

---

## 17. Security

- No credential handling.
- No secret logging.
- No broker session reuse.
- No environment reads.
- No file-system discovery of configuration secrets.
- Event payloads contain only already-public analytical facts.
- Deserialization rejects hostile or oversized malformed payloads by failing
  closed without executing embedded logic.

---

## 18. Lifecycle and integration

1. Construct immutable `BullPutSpreadConfiguration`.
2. Inject `StrategyScoringFramework`.
3. Register under `bull_put_spread`.
4. Evaluation Engine supplies `StrategyContext`.
5. Strategy returns `TradingSignal` via `run` and/or recommendation via evaluate APIs.
6. Trade Decision Engine consumes comparative reports.
7. Risk and Execution act only after independent approval.

### 18.1 Optional event topics

| Topic | When |
|---|---|
| `strategy.bull_put_spread.evaluated` | After sealed recommendation. |
| `strategy.bull_put_spread.abstained` | On ABSTAIN. |
| `strategy.bull_put_spread.rejected` | On REJECT. |
| `strategy.bull_put_spread.entered_candidate` | On ENTER candidate sealed. |

Event publication is observational only and MUST NOT gate the sealed result.

---

## 19. Testing

Unit tests live in `tests/test_bull_put_spread_strategy.py`.

Required coverage themes:

1. Configuration invariant failures.
2. Missing / stale snapshot rejects.
3. Regime and strong-bearish gates.
4. Unsuitable / low IV abstention.
5. Liquidity failure on each of the two legs.
6. Incomplete put-chain rejects.
7. Deterministic two-leg selection and geometry.
8. Net credit, max profit, max loss, POP calculations.
9. Minimum premium abstention.
10. Scoring integration happy path.
11. TradingSignal mapping with BULLISH direction and defined-risk metadata.
12. Serialization round trip and version rejection.
13. Concurrent identical evaluations.
14. Boundary greps proving no broker / order / risk imports.
15. Reversed chain order yields identical selection.

Target: greater than 95% line coverage of
`strategy/bull_put_spread_strategy.py`.

Tests MUST be deterministic and MUST NOT require network access.

---

## 20. Implementation checklist

- [ ] Create `strategy/bull_put_spread_strategy.py`.
- [ ] Create `tests/test_bull_put_spread_strategy.py`.
- [ ] Subclass `BaseStrategy`.
- [ ] Implement immutable configuration and public models.
- [ ] Implement gate ordering exactly as specified.
- [ ] Implement two-leg put strike selection.
- [ ] Implement defined-risk metrics.
- [ ] Integrate `StrategyScoringFramework` with `PREMIUM_SELLING`.
- [ ] Map ENTER/ABSTAIN/REJECT to `TradingSignal` with BULLISH direction.
- [ ] Provide versioned `to_json` / `from_json`.
- [ ] Enforce all BOUNDARY-BPS rules.
- [ ] Achieve >95% unit coverage.
- [ ] Register identity `bull_put_spread` without placing orders.

---

## 21. Definition of Done

This strategy is done only when it evaluates whether a Bull Put Spread is
appropriate and returns a structured recommendation compatible with the
existing Strategy Framework, and when it demonstrably does **not**:

- Place orders
- Modify orders
- Cancel orders
- Manage positions
- Calculate portfolio risk

Additional DoD checks:

- Architecture freeze boundaries remain intact.
- Defined-risk max loss is finite and labeled `DEFINED_RISK`.
- Two-leg put geometry is validated on every ENTER.
- ENTER direction is `BULLISH`.
- Unit coverage ≥ 95%.
- No unrelated modules modified unless strictly required by registration wiring
  explicitly requested in a later implementation task.

---

## 22. Non-goals

- Redesigning the Strategy Framework.
- Creating new framework modules.
- Building a broker multi-leg order composer.
- Training regime or IV models.
- Guaranteeing profitability.
- Managing adjustments, rolls, or exits.
- Implementing bear call spread logic inside this module.

---

## Appendix A — Worked NIFTY evaluation

Example (illustrative, not a live quote):

| Input | Value |
|---|---|
| Underlying | NIFTY |
| Spot | 24500 |
| Regime | TRENDING_UP |
| IV rank | 55 |
| Trend direction | BULLISH |
| Trend strength | 0.35 |
| Short target delta | 0.25 |
| Long target delta | 0.10 |

Selected structure (illustrative):

| Leg | Side | Type | Strike | Delta |
|---|---|---|---|---|
| 0 | SELL | PE | 24400 | -0.25 |
| 1 | BUY | PE | 24200 | -0.10 |

Derived metrics (illustrative):

| Metric | Value |
|---|---|
| Wing width | 200 |
| Net credit | 55 |
| Max profit | 55 × multiplier |
| Max loss | (200 - 55) × multiplier = 145 × multiplier |
| Max loss label | DEFINED_RISK |
| Lower breakeven | 24400 - 55 = 24345 |

---

## Appendix B — Candidate selection examples

| Scenario | Expected behavior |
|---|---|
| Two short puts equal delta error | Prefer tighter relative spread, then higher OI, then strike, then instrument id. |
| Long put only ATM/ITM above short | No eligible long; abstain/reject with no-eligible-long code. |
| Chain reversed | Identical sealed selection. |
| Duplicate conflicting instrument | Chain reject. |
| Call-only chain | Incomplete put chain reject/abstain. |

---

## Appendix C — IV rank examples

| IV rank | Floor | Outcome |
|---|---|---|
| 55 | 40 | Pass IV gate. |
| 40 | 40 | Pass IV gate. |
| 39.999 | 40 | Abstain `BPS.IV_RANK.LOW`. |
| missing | required | Reject `BPS.IV_RANK.MISSING`. |
| NaN | any | Reject `BPS.METRIC.NON_FINITE`. |

---

## Appendix D — Liquidity rejects

| Leg failing liquidity | Outcome |
|---|---|
| Short put | Structure abstains; no ENTER. |
| Long put | Structure abstains; no ENTER. |

Both legs MUST independently pass before ENTER.

---

## Appendix E — Factor bundle example

A passing ENTER factor bundle includes bullish regime suitability, non-bearish
trend alignment, elevated IV rank, two-leg put liquidity scores, short/long
delta proximity, defined reward/risk ratio, and non-adverse event evidence.
Provenance points to injected snapshot/tag/evidence identifiers only.

---

## Appendix F — TradingSignal example

An ENTER signal includes:

- `strategy_family = BULL_PUT_SPREAD`
- direction `BULLISH`
- structure hint with two PE legs in canonical order
- risk profile `DEFINED`
- `max_loss_category = DEFINED_RISK`
- sealed score / confidence / explanation references
- ordered reason codes beginning with passed gates and ending with
  `BPS.RISK.DEFINED`

---

## Appendix G — Failure matrix

| Failure | State | Continues to scoring? |
|---|---|---|
| Missing snapshot | REJECT | No |
| Stale snapshot | REJECT | No |
| Crisis regime | REJECT | No |
| Bearish regime | ABSTAIN | Optional abstention scoring only |
| Strong bearish trend | ABSTAIN | Optional abstention scoring only |
| Low / unsuitable IV | ABSTAIN | Optional abstention scoring only |
| Incomplete chain | REJECT | No |
| Poor liquidity | ABSTAIN | Optional abstention scoring only |
| Invalid geometry | REJECT | No |
| Below minimum premium | ABSTAIN | Optional abstention scoring only |

---

## Appendix H — Concurrency acceptance cases

| Case | Assertion |
|---|---|
| Same strategy, two threads, identical context | Byte-equivalent canonical JSON. |
| Same strategy, two threads, different underlyings | Isolated recommendations; no cross-talk. |
| Event sink raises in one thread | Other thread unaffected; failing sink does not mutate result. |

---

## Appendix I — Glossary

| Term | Meaning |
|---|---|
| Bull Put Spread | Two-leg defined-risk short put vertical (credit). |
| Short put | Sold higher-strike put. |
| Long put / wing | Bought lower-strike put that caps loss. |
| Wing width | Absolute strike distance between short and long puts. |
| Defined risk | Maximum loss finite and known from geometry and credit. |
| POP heuristic | Transparent ranking probability, not a guarantee. |
| ENTER | Analytical candidate suitable for downstream decisioning. |

---

## Appendix J — Legacy migration

No legacy bull-put-spread plugin is assumed. If a prototype exists outside this
module path, it MUST NOT be imported. Consumers migrate by registering
`BullPutSpreadStrategy` under `bull_put_spread` and consuming sealed
recommendations.

---

## Appendix K — Benchmark contract

| Metric | Expectation |
|---|---|
| Evaluation complexity | Linear in option-chain size for filtering; selection uses deterministic min over filtered candidates. |
| Allocation | No unbounded per-call global caches. |
| External I/O | Zero. |

---

## Appendix L — Default profile rationale

Defaults favor moderately elevated IV, short deltas near 25-delta, long wings
near 10-delta, and explicit strong-bearish rejection. These defaults are
suitability priors for Indian index options and remain configurable.

---

## Appendix M — Delta interpretation

Put deltas are expected non-positive. Comparisons use absolute magnitudes.
Sign violations on selected legs reject geometry or Greeks validation.

---

## Appendix N — POP notes

POP is intentionally conservative and transparent. It uses short-put OTM
heuristics plus small credit and defined-risk adjustments. It MUST NEVER be
labeled as Black-Scholes probability or backtested expectancy.

---

## Appendix O — Structured reason catalog

| Reason | Meaning |
|---|---|
| `BPS.GATES.PASS` | All mandatory gates passed. |
| `BPS.RISK.DEFINED` | Defined-risk statement attached. |
| `BPS.STRUCTURE.TWO_LEGS` | Two-leg put structure sealed. |
| `BPS.REGIME.BULLISH` | Bullish / trending-up evidence observed. |
| `BPS.IV_RANK.PASS` | IV rank above floor. |
| `BPS.TREND.PASS` | Trend evidence not strongly bearish. |
| `BPS.LIQUIDITY.PASS` | Two-leg liquidity passed. |

---

## Appendix P — Audit fields

Every recommendation SHOULD make the following auditable:

- recommendation id
- as-of timestamp
- underlying
- regime tag
- iv rank
- trend direction / strength
- selected strikes
- net credit / max profit / max loss
- reason codes
- schema version

---

## Appendix Q — Observability boundaries

Logs and events may include analytical facts already present in the sealed
recommendation. They MUST NOT include secrets, account identifiers, or broker
request payloads.

---

## Appendix R — Numerical rules

- Use `Decimal` for money, strikes, deltas, IV rank, and ratios.
- Reject non-finite values.
- Normalize before compare.
- Round only at sealing boundaries using documented quanta.

---

## Appendix S — Time rules

- Context `as_of` is authoritative.
- Convert to configured exchange timezone for window checks.
- Entry window is `[start, end)`.
- Exit window is metadata only.
- No sleeping, scheduling, or clock polling.

---

## Appendix T — Data provenance

Every factor and metric MUST identify whether it came from:

- `MarketSnapshot`
- injected tags
- typed evidence objects
- scoring framework outputs

No silent broker enrichment is permitted.

---

## Appendix U — Compatibility notes

| Consumer | Compatibility requirement |
|---|---|
| Strategy Evaluation Engine | `BaseStrategy.run` returns `TradingSignal`. |
| Trade Decision Engine | Consumes comparative reports; this module does not self-approve. |
| Execution Engine | May later map two-leg structure hints; this module never places orders. |
| Scoring Framework | `PREMIUM_SELLING` profile inputs only. |

---

## Appendix V — Rejection precedence

When multiple failures exist, emit the first failure in gate order. Do not
mask an earlier REJECT with a later ABSTAIN. Do not continue strike selection
after a hard REJECT.

---

## Appendix W — Entry payload constraints

ENTER payloads MUST include:

- two distinct put instrument identities
- shared expiry
- valid geometry (`long_put < short_put`)
- positive net credit
- positive finite max loss
- `DEFINED_RISK` label
- BULLISH direction on the signal
- score / confidence / explanation when scoring succeeds

---

## Appendix X — Exit metadata constraints

Exit-window configuration may be copied into metadata for downstream managers.
This module MUST NOT generate exit orders, stop orders, or adjust orders.

---

## Appendix Y — Implementation hazards

| Hazard | Required mitigation |
|---|---|
| Emitting naked short put | Hard-require two legs and geometry validation. |
| Treating max loss as undefined | Always seal finite max loss and DEFINED_RISK. |
| Using ask-only credit without debit leg | Apply consistent two-leg price policy. |
| Ignoring strong bearish trend | Enforce bearish-trend gate before ENTER. |
| Selecting calls | Restrict candidates to PE only. |
| Mutating snapshot contracts | Use immutable reads only. |
| Non-deterministic dict iteration | Sort by documented keys. |

---

## Appendix Z — Changelog

| Version | Date | Notes |
|---|---|---|
| 1.0.0 | 2026-08-05 | Initial locked-contract specification for defined-risk bull put spread. |

---

## 23. Detailed evaluation algorithm

The evaluation algorithm is the normative control flow for
`BullPutSpreadStrategy._evaluate_result`.

### 23.1 Resolve context

1. If input is `BullPutSpreadContext`, unwrap `StrategyContext` and typed evidence.
2. Else treat input as `StrategyContext` and parse optional tags.
3. Normalize underlying identity to uppercase.
4. Capture `as_of` as the sole temporal authority.

### 23.2 Validate context

1. Reject missing snapshot (`BPS.SNAPSHOT.MISSING`).
2. Reject invalid snapshot identity when `require_valid_snapshot` is true.
3. Reject unsupported underlying (`BPS.UNDERLYING.UNSUPPORTED`).
4. Reject malformed tags that claim to be numeric but are not finite decimals.

### 23.3 Freshness

Compare `snapshot.freshness.age_seconds` with `max_snapshot_age_seconds`.
If greater, reject `BPS.SNAPSHOT.STALE`.

### 23.4 Time window

Convert `as_of` into the configured timezone. If local time is outside
`[start, end)`, abstain `BPS.TIME.OUTSIDE_ENTRY_WINDOW`.

### 23.5 Regime and events

1. Missing regime → reject.
2. Crisis → reject.
3. Unsuitable → abstain.
4. Adverse event → abstain.

### 23.6 Trend evidence

1. Missing required trend evidence → reject.
2. Strong bearish direction or strength ceiling breach → abstain.
3. Else pass and record `BPS.TREND.PASS`.

### 23.7 IV rank

1. Missing required IV rank → reject.
2. Non-finite / out of `[0, 100]` → reject.
3. Below floor → abstain.
4. Else pass and record `BPS.IV_RANK.PASS`.

### 23.8 Select structure

1. Select expiry.
2. Select short put.
3. Select long put.
4. Validate geometry.
5. Validate per-leg liquidity on the final two.
6. Compute credit and risk metrics.
7. Enforce minimum premium and positive max loss.

### 23.9 Score and seal

1. Build factor input bundle with provenance.
2. Call scoring framework.
3. On framework failure, reject `BPS.SCORING.FAILED`.
4. Map recommendation and BULLISH signal.
5. Optionally publish observational events.
6. Return immutable `BullPutSpreadEvaluationResult`.

---

## 24. Interaction with PortfolioSnapshot and RiskProfile

### 24.1 PortfolioSnapshot

If injected, the strategy may copy non-sensitive identifiers into explanation
metadata for audit correlation. It MUST NOT:

- mark-to-market the portfolio
- net exposures
- compute buying power
- block entry based on portfolio utilization

Portfolio enforcement belongs to Risk and Trade Decision components.

### 24.2 RiskProfile

If injected, the strategy may record the preferred risk posture as
informational context. It MUST NOT:

- override defined-risk geometry
- resize the structure
- approve or deny the trade authoritatively

### 24.3 HistoricalData

Injected historical series may be used only for configured fallback IV-rank
derivation. The strategy MUST NOT fetch additional history. Fallback derivation
MUST record provenance distinguishing injected rank from derived rank.

---

## 25. Structure-hint contract for consumers

```text
StructureHint(
  structure_type="bull_put_spread",
  leg_count=2,
  selection_method="delta_ranked_put_vertical",
  target_delta=float(short_target_delta),
  quantity_hint=1,
  option_types=(PE, PE),
)
```

Exact field names MUST follow the existing `StructureHint` model in
`strategy/signals.py`. Additional leg identity details may be placed in signal
metadata using stable keys:

- `leg0_instrument_id`, `leg1_instrument_id`
- `leg0_strike`, `leg1_strike`
- `leg0_side=SELL`, `leg1_side=BUY`
- `wing_width`
- `net_credit`
- `max_loss`
- `max_loss_label=DEFINED_RISK`

---

## 26. Comparison with related strategies

| Dimension | Bull Put Spread | Bear Call Spread | Iron Condor | Short Strangle |
|---|---|---|---|---|
| Legs | 2 puts | 2 calls | 4 legs | 2 shorts |
| Direction | BULLISH | BEARISH | NEUTRAL / SHORT_VOL | NEUTRAL / SHORT_VOL |
| Max loss | DEFINED_RISK | DEFINED_RISK | DEFINED_RISK | UNDEFINED_UNLIMITED |
| Strong opposite trend | Strong bearish abstain | Strong bullish abstain | High trend strength abstain | Trend unsuitable abstain |
| Execution map | SELL, BUY | SELL, BUY | SELL, BUY, SELL, BUY | SELL, SELL |

The strategies remain independent plugins. Neither imports another for decision
logic. Shared utilities may be duplicated locally when needed to avoid creating
new framework modules.

---

## 27. Explicit prohibition list for implementers

Implementers MUST NOT:

1. Create `strategy/framework_*.py` modules.
2. Redesign `BaseStrategy`.
3. Add broker clients under `strategy/`.
4. Call order placement helpers.
5. Soft-fail geometry errors into ABSTAIN when geometry is invalid.
6. Emit ENTER with fewer than two put legs.
7. Emit ENTER with non-positive max loss.
8. Emit ENTER with non-BULLISH direction.
9. Use float binary equality for strike comparisons.
10. Read `datetime.now()` or `time.time()`.
11. Seed randomness for tie-breaks.
12. Persist recommendations to disk.
13. Mutate `tags` or snapshot collections.
14. Treat POP as a hard risk limit.
15. Suppress defined-risk warnings.
16. Depend on dictionary insertion order for ranking.
17. Select call contracts for this strategy.

---

## 28. Recommended unit-test fixture recipe

1. Build a `MarketSnapshot` with finite spot and freshness age within limit.
2. Provide OTM PE contracts across at least two wing distances.
3. Include bid/ask/OI/volume/delta for every candidate.
4. Set `as_of` inside the entry window in the configured timezone.
5. Supply regime `TRENDING_UP` or `BULLISH`, IV rank above floor, non-bearish trend.
6. Assert ENTER, two strikes, positive credit, positive max loss, DEFINED_RISK, BULLISH.
7. Mutate one gate at a time and assert the documented code/state.
8. Reverse contract order and assert identical sealed JSON.
9. Run concurrent evaluations and assert isolation.
10. Grep module source for forbidden imports and APIs.

---

## 29. Serialization schema sketch

```json
{
  "schema_version": "1.0.0",
  "recommendation_id": "...",
  "state": "ENTER",
  "strategy_id": "bull_put_spread",
  "as_of": "2026-08-05T05:00:00+00:00",
  "reasons": ["BPS.GATES.PASS", "BPS.RISK.DEFINED", "BPS.STRUCTURE.TWO_LEGS"],
  "strike_selection": {
    "underlying": "NIFTY",
    "spot": "24500",
    "expiry": "2026-08-13",
    "short_put_strike": "24400",
    "long_put_strike": "24200",
    "wing_width": "200"
  },
  "risk_metrics": {
    "net_credit": "55",
    "max_profit": "55",
    "max_loss": "145",
    "max_loss_label": "DEFINED_RISK",
    "probability_of_profit": "0.74",
    "reward_risk_ratio": "0.3793103448275862"
  }
}
```

Field sets are normative in intent; exact key inventory must match the frozen
models in Section 11 and the module schema constant.

---

## 30. Acceptance sign-off matrix

| Role | Signs off on |
|---|---|
| Strategy implementer | Code matches this contract. |
| Evaluation-engine owner | Plugin run contract compatibility. |
| Trade-decision owner | Recommendation/signal consumability. |
| Risk owner | Confirmation that plugin does not calculate portfolio risk. |
| Execution owner | Confirmation that plugin does not place/modify/cancel orders. |
| QA | >95% coverage and boundary greps. |

---

---
## Appendices AA–BD — Normative verification and operations catalog
These appendices define compact, independently executable acceptance vectors. Each vector is a required unit, integration, or property test scenario; it is not an instruction to perform I/O.

## Appendix AA — Validation vector catalog
The vectors below verify configuration and context validation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AA-V01 | nominal valid input for configuration and context validation | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V02 | required field absent for configuration and context validation | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V03 | non-finite numeric value for configuration and context validation | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V04 | boundary value equal to minimum for configuration and context validation | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V05 | value one quantum below minimum for configuration and context validation | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V06 | value one quantum above maximum for configuration and context validation | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V07 | timestamp exactly at start boundary for configuration and context validation | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V08 | timestamp exactly at end boundary for configuration and context validation | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V09 | two otherwise equal candidates for configuration and context validation | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V10 | input collection reversed for configuration and context validation | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V11 | unrelated metadata added for configuration and context validation | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V12 | optional metadata absent for configuration and context validation | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V13 | optional metadata malformed for configuration and context validation | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V14 | immutable input reused for configuration and context validation | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V15 | same input evaluated twice for configuration and context validation | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V16 | same input evaluated concurrently for configuration and context validation | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V17 | event sink raises exception for configuration and context validation | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V18 | unknown enum supplied for configuration and context validation | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V19 | unknown payload field supplied for configuration and context validation | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V20 | unsupported underlying supplied for configuration and context validation | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V21 | snapshot id conflicts with contract identity for configuration and context validation | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V22 | selected contract token absent for configuration and context validation | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V23 | symbol absent but immutable instrument id present for configuration and context validation | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V24 | decimal precision exceeds display precision for configuration and context validation | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V25 | negative zero submitted for configuration and context validation | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V26 | large but finite option chain for configuration and context validation | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V27 | duplicate contract id with same facts for configuration and context validation | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V28 | duplicate contract id with conflicting facts for configuration and context validation | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V29 | score framework rejects factor bundle for configuration and context validation | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V30 | score framework returns sealed score for configuration and context validation | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V31 | RiskProfile prefers no entry for configuration and context validation | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V32 | PortfolioSnapshot contains exposure for configuration and context validation | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V33 | HistoricalSeries is injected for configuration and context validation | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V34 | broker adapter is available in process for configuration and context validation | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V35 | environment contains credentials for configuration and context validation | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V36 | order manager exists in composition for configuration and context validation | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V37 | risk engine exists in composition for configuration and context validation | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V38 | trade decision exists downstream for configuration and context validation | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V39 | serialization round trip for configuration and context validation | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AA-V40 | schema version incompatible for configuration and context validation | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AA-ACCEPT-001:** All forty vectors pass without external calls.
**AA-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AA-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AB — Snapshot freshness catalog
The vectors below verify snapshot identity and freshness. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AB-V01 | nominal valid input for snapshot identity and freshness | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V02 | required field absent for snapshot identity and freshness | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V03 | non-finite numeric value for snapshot identity and freshness | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V04 | boundary value equal to minimum for snapshot identity and freshness | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V05 | value one quantum below minimum for snapshot identity and freshness | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V06 | value one quantum above maximum for snapshot identity and freshness | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V07 | timestamp exactly at start boundary for snapshot identity and freshness | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V08 | timestamp exactly at end boundary for snapshot identity and freshness | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V09 | two otherwise equal candidates for snapshot identity and freshness | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V10 | input collection reversed for snapshot identity and freshness | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V11 | unrelated metadata added for snapshot identity and freshness | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V12 | optional metadata absent for snapshot identity and freshness | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V13 | optional metadata malformed for snapshot identity and freshness | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V14 | immutable input reused for snapshot identity and freshness | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V15 | same input evaluated twice for snapshot identity and freshness | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V16 | same input evaluated concurrently for snapshot identity and freshness | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V17 | event sink raises exception for snapshot identity and freshness | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V18 | unknown enum supplied for snapshot identity and freshness | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V19 | unknown payload field supplied for snapshot identity and freshness | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V20 | unsupported underlying supplied for snapshot identity and freshness | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V21 | snapshot id conflicts with contract identity for snapshot identity and freshness | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V22 | selected contract token absent for snapshot identity and freshness | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V23 | symbol absent but immutable instrument id present for snapshot identity and freshness | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V24 | decimal precision exceeds display precision for snapshot identity and freshness | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V25 | negative zero submitted for snapshot identity and freshness | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V26 | large but finite option chain for snapshot identity and freshness | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V27 | duplicate contract id with same facts for snapshot identity and freshness | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V28 | duplicate contract id with conflicting facts for snapshot identity and freshness | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V29 | score framework rejects factor bundle for snapshot identity and freshness | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V30 | score framework returns sealed score for snapshot identity and freshness | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V31 | RiskProfile prefers no entry for snapshot identity and freshness | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V32 | PortfolioSnapshot contains exposure for snapshot identity and freshness | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V33 | HistoricalSeries is injected for snapshot identity and freshness | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V34 | broker adapter is available in process for snapshot identity and freshness | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V35 | environment contains credentials for snapshot identity and freshness | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V36 | order manager exists in composition for snapshot identity and freshness | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V37 | risk engine exists in composition for snapshot identity and freshness | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V38 | trade decision exists downstream for snapshot identity and freshness | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V39 | serialization round trip for snapshot identity and freshness | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AB-V40 | schema version incompatible for snapshot identity and freshness | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AB-ACCEPT-001:** All forty vectors pass without external calls.
**AB-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AB-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AC — Regime gate catalog
The vectors below verify bullish and mildly-bullish regime suitability. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AC-V01 | nominal valid input for bullish and mildly-bullish regime suitability | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V02 | required field absent for bullish and mildly-bullish regime suitability | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V03 | non-finite numeric value for bullish and mildly-bullish regime suitability | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V04 | boundary value equal to minimum for bullish and mildly-bullish regime suitability | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V05 | value one quantum below minimum for bullish and mildly-bullish regime suitability | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V06 | value one quantum above maximum for bullish and mildly-bullish regime suitability | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V07 | timestamp exactly at start boundary for bullish and mildly-bullish regime suitability | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V08 | timestamp exactly at end boundary for bullish and mildly-bullish regime suitability | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V09 | two otherwise equal candidates for bullish and mildly-bullish regime suitability | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V10 | input collection reversed for bullish and mildly-bullish regime suitability | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V11 | unrelated metadata added for bullish and mildly-bullish regime suitability | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V12 | optional metadata absent for bullish and mildly-bullish regime suitability | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V13 | optional metadata malformed for bullish and mildly-bullish regime suitability | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V14 | immutable input reused for bullish and mildly-bullish regime suitability | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V15 | same input evaluated twice for bullish and mildly-bullish regime suitability | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V16 | same input evaluated concurrently for bullish and mildly-bullish regime suitability | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V17 | event sink raises exception for bullish and mildly-bullish regime suitability | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V18 | unknown enum supplied for bullish and mildly-bullish regime suitability | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V19 | unknown payload field supplied for bullish and mildly-bullish regime suitability | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V20 | unsupported underlying supplied for bullish and mildly-bullish regime suitability | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V21 | snapshot id conflicts with contract identity for bullish and mildly-bullish regime suitability | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V22 | selected contract token absent for bullish and mildly-bullish regime suitability | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V23 | symbol absent but immutable instrument id present for bullish and mildly-bullish regime suitability | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V24 | decimal precision exceeds display precision for bullish and mildly-bullish regime suitability | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V25 | negative zero submitted for bullish and mildly-bullish regime suitability | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V26 | large but finite option chain for bullish and mildly-bullish regime suitability | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V27 | duplicate contract id with same facts for bullish and mildly-bullish regime suitability | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V28 | duplicate contract id with conflicting facts for bullish and mildly-bullish regime suitability | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V29 | score framework rejects factor bundle for bullish and mildly-bullish regime suitability | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V30 | score framework returns sealed score for bullish and mildly-bullish regime suitability | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V31 | RiskProfile prefers no entry for bullish and mildly-bullish regime suitability | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V32 | PortfolioSnapshot contains exposure for bullish and mildly-bullish regime suitability | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V33 | HistoricalSeries is injected for bullish and mildly-bullish regime suitability | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V34 | broker adapter is available in process for bullish and mildly-bullish regime suitability | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V35 | environment contains credentials for bullish and mildly-bullish regime suitability | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V36 | order manager exists in composition for bullish and mildly-bullish regime suitability | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V37 | risk engine exists in composition for bullish and mildly-bullish regime suitability | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V38 | trade decision exists downstream for bullish and mildly-bullish regime suitability | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V39 | serialization round trip for bullish and mildly-bullish regime suitability | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AC-V40 | schema version incompatible for bullish and mildly-bullish regime suitability | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AC-ACCEPT-001:** All forty vectors pass without external calls.
**AC-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AC-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AD — Bearish-trend rejection catalog
The vectors below verify strong bearish trend rejection. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AD-V01 | nominal valid input for strong bearish trend rejection | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V02 | required field absent for strong bearish trend rejection | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V03 | non-finite numeric value for strong bearish trend rejection | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V04 | boundary value equal to minimum for strong bearish trend rejection | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V05 | value one quantum below minimum for strong bearish trend rejection | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V06 | value one quantum above maximum for strong bearish trend rejection | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V07 | timestamp exactly at start boundary for strong bearish trend rejection | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V08 | timestamp exactly at end boundary for strong bearish trend rejection | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V09 | two otherwise equal candidates for strong bearish trend rejection | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V10 | input collection reversed for strong bearish trend rejection | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V11 | unrelated metadata added for strong bearish trend rejection | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V12 | optional metadata absent for strong bearish trend rejection | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V13 | optional metadata malformed for strong bearish trend rejection | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V14 | immutable input reused for strong bearish trend rejection | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V15 | same input evaluated twice for strong bearish trend rejection | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V16 | same input evaluated concurrently for strong bearish trend rejection | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V17 | event sink raises exception for strong bearish trend rejection | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V18 | unknown enum supplied for strong bearish trend rejection | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V19 | unknown payload field supplied for strong bearish trend rejection | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V20 | unsupported underlying supplied for strong bearish trend rejection | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V21 | snapshot id conflicts with contract identity for strong bearish trend rejection | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V22 | selected contract token absent for strong bearish trend rejection | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V23 | symbol absent but immutable instrument id present for strong bearish trend rejection | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V24 | decimal precision exceeds display precision for strong bearish trend rejection | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V25 | negative zero submitted for strong bearish trend rejection | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V26 | large but finite option chain for strong bearish trend rejection | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V27 | duplicate contract id with same facts for strong bearish trend rejection | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V28 | duplicate contract id with conflicting facts for strong bearish trend rejection | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V29 | score framework rejects factor bundle for strong bearish trend rejection | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V30 | score framework returns sealed score for strong bearish trend rejection | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V31 | RiskProfile prefers no entry for strong bearish trend rejection | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V32 | PortfolioSnapshot contains exposure for strong bearish trend rejection | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V33 | HistoricalSeries is injected for strong bearish trend rejection | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V34 | broker adapter is available in process for strong bearish trend rejection | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V35 | environment contains credentials for strong bearish trend rejection | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V36 | order manager exists in composition for strong bearish trend rejection | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V37 | risk engine exists in composition for strong bearish trend rejection | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V38 | trade decision exists downstream for strong bearish trend rejection | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V39 | serialization round trip for strong bearish trend rejection | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AD-V40 | schema version incompatible for strong bearish trend rejection | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AD-ACCEPT-001:** All forty vectors pass without external calls.
**AD-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AD-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AE — Trend-strength catalog
The vectors below verify bullish trend-strength suitability. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AE-V01 | nominal valid input for bullish trend-strength suitability | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V02 | required field absent for bullish trend-strength suitability | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V03 | non-finite numeric value for bullish trend-strength suitability | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V04 | boundary value equal to minimum for bullish trend-strength suitability | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V05 | value one quantum below minimum for bullish trend-strength suitability | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V06 | value one quantum above maximum for bullish trend-strength suitability | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V07 | timestamp exactly at start boundary for bullish trend-strength suitability | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V08 | timestamp exactly at end boundary for bullish trend-strength suitability | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V09 | two otherwise equal candidates for bullish trend-strength suitability | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V10 | input collection reversed for bullish trend-strength suitability | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V11 | unrelated metadata added for bullish trend-strength suitability | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V12 | optional metadata absent for bullish trend-strength suitability | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V13 | optional metadata malformed for bullish trend-strength suitability | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V14 | immutable input reused for bullish trend-strength suitability | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V15 | same input evaluated twice for bullish trend-strength suitability | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V16 | same input evaluated concurrently for bullish trend-strength suitability | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V17 | event sink raises exception for bullish trend-strength suitability | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V18 | unknown enum supplied for bullish trend-strength suitability | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V19 | unknown payload field supplied for bullish trend-strength suitability | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V20 | unsupported underlying supplied for bullish trend-strength suitability | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V21 | snapshot id conflicts with contract identity for bullish trend-strength suitability | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V22 | selected contract token absent for bullish trend-strength suitability | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V23 | symbol absent but immutable instrument id present for bullish trend-strength suitability | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V24 | decimal precision exceeds display precision for bullish trend-strength suitability | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V25 | negative zero submitted for bullish trend-strength suitability | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V26 | large but finite option chain for bullish trend-strength suitability | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V27 | duplicate contract id with same facts for bullish trend-strength suitability | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V28 | duplicate contract id with conflicting facts for bullish trend-strength suitability | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V29 | score framework rejects factor bundle for bullish trend-strength suitability | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V30 | score framework returns sealed score for bullish trend-strength suitability | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V31 | RiskProfile prefers no entry for bullish trend-strength suitability | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V32 | PortfolioSnapshot contains exposure for bullish trend-strength suitability | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V33 | HistoricalSeries is injected for bullish trend-strength suitability | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V34 | broker adapter is available in process for bullish trend-strength suitability | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V35 | environment contains credentials for bullish trend-strength suitability | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V36 | order manager exists in composition for bullish trend-strength suitability | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V37 | risk engine exists in composition for bullish trend-strength suitability | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V38 | trade decision exists downstream for bullish trend-strength suitability | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V39 | serialization round trip for bullish trend-strength suitability | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AE-V40 | schema version incompatible for bullish trend-strength suitability | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AE-ACCEPT-001:** All forty vectors pass without external calls.
**AE-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AE-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AF — IV and IV-rank catalog
The vectors below verify IV and IV-rank suitability. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AF-V01 | nominal valid input for IV and IV-rank suitability | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V02 | required field absent for IV and IV-rank suitability | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V03 | non-finite numeric value for IV and IV-rank suitability | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V04 | boundary value equal to minimum for IV and IV-rank suitability | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V05 | value one quantum below minimum for IV and IV-rank suitability | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V06 | value one quantum above maximum for IV and IV-rank suitability | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V07 | timestamp exactly at start boundary for IV and IV-rank suitability | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V08 | timestamp exactly at end boundary for IV and IV-rank suitability | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V09 | two otherwise equal candidates for IV and IV-rank suitability | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V10 | input collection reversed for IV and IV-rank suitability | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V11 | unrelated metadata added for IV and IV-rank suitability | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V12 | optional metadata absent for IV and IV-rank suitability | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V13 | optional metadata malformed for IV and IV-rank suitability | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V14 | immutable input reused for IV and IV-rank suitability | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V15 | same input evaluated twice for IV and IV-rank suitability | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V16 | same input evaluated concurrently for IV and IV-rank suitability | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V17 | event sink raises exception for IV and IV-rank suitability | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V18 | unknown enum supplied for IV and IV-rank suitability | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V19 | unknown payload field supplied for IV and IV-rank suitability | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V20 | unsupported underlying supplied for IV and IV-rank suitability | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V21 | snapshot id conflicts with contract identity for IV and IV-rank suitability | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V22 | selected contract token absent for IV and IV-rank suitability | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V23 | symbol absent but immutable instrument id present for IV and IV-rank suitability | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V24 | decimal precision exceeds display precision for IV and IV-rank suitability | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V25 | negative zero submitted for IV and IV-rank suitability | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V26 | large but finite option chain for IV and IV-rank suitability | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V27 | duplicate contract id with same facts for IV and IV-rank suitability | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V28 | duplicate contract id with conflicting facts for IV and IV-rank suitability | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V29 | score framework rejects factor bundle for IV and IV-rank suitability | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V30 | score framework returns sealed score for IV and IV-rank suitability | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V31 | RiskProfile prefers no entry for IV and IV-rank suitability | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V32 | PortfolioSnapshot contains exposure for IV and IV-rank suitability | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V33 | HistoricalSeries is injected for IV and IV-rank suitability | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V34 | broker adapter is available in process for IV and IV-rank suitability | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V35 | environment contains credentials for IV and IV-rank suitability | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V36 | order manager exists in composition for IV and IV-rank suitability | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V37 | risk engine exists in composition for IV and IV-rank suitability | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V38 | trade decision exists downstream for IV and IV-rank suitability | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V39 | serialization round trip for IV and IV-rank suitability | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AF-V40 | schema version incompatible for IV and IV-rank suitability | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AF-ACCEPT-001:** All forty vectors pass without external calls.
**AF-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AF-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AG — Liquidity gate catalog
The vectors below verify two-leg put liquidity thresholds. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AG-V01 | nominal valid input for two-leg put liquidity thresholds | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V02 | required field absent for two-leg put liquidity thresholds | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V03 | non-finite numeric value for two-leg put liquidity thresholds | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V04 | boundary value equal to minimum for two-leg put liquidity thresholds | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V05 | value one quantum below minimum for two-leg put liquidity thresholds | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V06 | value one quantum above maximum for two-leg put liquidity thresholds | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V07 | timestamp exactly at start boundary for two-leg put liquidity thresholds | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V08 | timestamp exactly at end boundary for two-leg put liquidity thresholds | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V09 | two otherwise equal candidates for two-leg put liquidity thresholds | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V10 | input collection reversed for two-leg put liquidity thresholds | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V11 | unrelated metadata added for two-leg put liquidity thresholds | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V12 | optional metadata absent for two-leg put liquidity thresholds | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V13 | optional metadata malformed for two-leg put liquidity thresholds | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V14 | immutable input reused for two-leg put liquidity thresholds | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V15 | same input evaluated twice for two-leg put liquidity thresholds | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V16 | same input evaluated concurrently for two-leg put liquidity thresholds | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V17 | event sink raises exception for two-leg put liquidity thresholds | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V18 | unknown enum supplied for two-leg put liquidity thresholds | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V19 | unknown payload field supplied for two-leg put liquidity thresholds | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V20 | unsupported underlying supplied for two-leg put liquidity thresholds | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V21 | snapshot id conflicts with contract identity for two-leg put liquidity thresholds | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V22 | selected contract token absent for two-leg put liquidity thresholds | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V23 | symbol absent but immutable instrument id present for two-leg put liquidity thresholds | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V24 | decimal precision exceeds display precision for two-leg put liquidity thresholds | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V25 | negative zero submitted for two-leg put liquidity thresholds | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V26 | large but finite option chain for two-leg put liquidity thresholds | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V27 | duplicate contract id with same facts for two-leg put liquidity thresholds | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V28 | duplicate contract id with conflicting facts for two-leg put liquidity thresholds | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V29 | score framework rejects factor bundle for two-leg put liquidity thresholds | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V30 | score framework returns sealed score for two-leg put liquidity thresholds | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V31 | RiskProfile prefers no entry for two-leg put liquidity thresholds | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V32 | PortfolioSnapshot contains exposure for two-leg put liquidity thresholds | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V33 | HistoricalSeries is injected for two-leg put liquidity thresholds | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V34 | broker adapter is available in process for two-leg put liquidity thresholds | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V35 | environment contains credentials for two-leg put liquidity thresholds | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V36 | order manager exists in composition for two-leg put liquidity thresholds | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V37 | risk engine exists in composition for two-leg put liquidity thresholds | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V38 | trade decision exists downstream for two-leg put liquidity thresholds | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V39 | serialization round trip for two-leg put liquidity thresholds | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AG-V40 | schema version incompatible for two-leg put liquidity thresholds | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AG-ACCEPT-001:** All forty vectors pass without external calls.
**AG-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AG-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AH — Time-window catalog
The vectors below verify entry and informational exit windows. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AH-V01 | nominal valid input for entry and informational exit windows | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V02 | required field absent for entry and informational exit windows | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V03 | non-finite numeric value for entry and informational exit windows | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V04 | boundary value equal to minimum for entry and informational exit windows | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V05 | value one quantum below minimum for entry and informational exit windows | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V06 | value one quantum above maximum for entry and informational exit windows | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V07 | timestamp exactly at start boundary for entry and informational exit windows | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V08 | timestamp exactly at end boundary for entry and informational exit windows | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V09 | two otherwise equal candidates for entry and informational exit windows | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V10 | input collection reversed for entry and informational exit windows | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V11 | unrelated metadata added for entry and informational exit windows | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V12 | optional metadata absent for entry and informational exit windows | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V13 | optional metadata malformed for entry and informational exit windows | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V14 | immutable input reused for entry and informational exit windows | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V15 | same input evaluated twice for entry and informational exit windows | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V16 | same input evaluated concurrently for entry and informational exit windows | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V17 | event sink raises exception for entry and informational exit windows | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V18 | unknown enum supplied for entry and informational exit windows | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V19 | unknown payload field supplied for entry and informational exit windows | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V20 | unsupported underlying supplied for entry and informational exit windows | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V21 | snapshot id conflicts with contract identity for entry and informational exit windows | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V22 | selected contract token absent for entry and informational exit windows | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V23 | symbol absent but immutable instrument id present for entry and informational exit windows | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V24 | decimal precision exceeds display precision for entry and informational exit windows | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V25 | negative zero submitted for entry and informational exit windows | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V26 | large but finite option chain for entry and informational exit windows | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V27 | duplicate contract id with same facts for entry and informational exit windows | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V28 | duplicate contract id with conflicting facts for entry and informational exit windows | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V29 | score framework rejects factor bundle for entry and informational exit windows | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V30 | score framework returns sealed score for entry and informational exit windows | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V31 | RiskProfile prefers no entry for entry and informational exit windows | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V32 | PortfolioSnapshot contains exposure for entry and informational exit windows | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V33 | HistoricalSeries is injected for entry and informational exit windows | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V34 | broker adapter is available in process for entry and informational exit windows | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V35 | environment contains credentials for entry and informational exit windows | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V36 | order manager exists in composition for entry and informational exit windows | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V37 | risk engine exists in composition for entry and informational exit windows | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V38 | trade decision exists downstream for entry and informational exit windows | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V39 | serialization round trip for entry and informational exit windows | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AH-V40 | schema version incompatible for entry and informational exit windows | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AH-ACCEPT-001:** All forty vectors pass without external calls.
**AH-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AH-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AI — Chain-completeness catalog
The vectors below verify put-chain completeness for two legs. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AI-V01 | nominal valid input for put-chain completeness for two legs | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V02 | required field absent for put-chain completeness for two legs | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V03 | non-finite numeric value for put-chain completeness for two legs | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V04 | boundary value equal to minimum for put-chain completeness for two legs | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V05 | value one quantum below minimum for put-chain completeness for two legs | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V06 | value one quantum above maximum for put-chain completeness for two legs | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V07 | timestamp exactly at start boundary for put-chain completeness for two legs | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V08 | timestamp exactly at end boundary for put-chain completeness for two legs | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V09 | two otherwise equal candidates for put-chain completeness for two legs | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V10 | input collection reversed for put-chain completeness for two legs | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V11 | unrelated metadata added for put-chain completeness for two legs | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V12 | optional metadata absent for put-chain completeness for two legs | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V13 | optional metadata malformed for put-chain completeness for two legs | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V14 | immutable input reused for put-chain completeness for two legs | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V15 | same input evaluated twice for put-chain completeness for two legs | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V16 | same input evaluated concurrently for put-chain completeness for two legs | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V17 | event sink raises exception for put-chain completeness for two legs | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V18 | unknown enum supplied for put-chain completeness for two legs | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V19 | unknown payload field supplied for put-chain completeness for two legs | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V20 | unsupported underlying supplied for put-chain completeness for two legs | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V21 | snapshot id conflicts with contract identity for put-chain completeness for two legs | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V22 | selected contract token absent for put-chain completeness for two legs | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V23 | symbol absent but immutable instrument id present for put-chain completeness for two legs | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V24 | decimal precision exceeds display precision for put-chain completeness for two legs | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V25 | negative zero submitted for put-chain completeness for two legs | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V26 | large but finite option chain for put-chain completeness for two legs | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V27 | duplicate contract id with same facts for put-chain completeness for two legs | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V28 | duplicate contract id with conflicting facts for put-chain completeness for two legs | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V29 | score framework rejects factor bundle for put-chain completeness for two legs | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V30 | score framework returns sealed score for put-chain completeness for two legs | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V31 | RiskProfile prefers no entry for put-chain completeness for two legs | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V32 | PortfolioSnapshot contains exposure for put-chain completeness for two legs | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V33 | HistoricalSeries is injected for put-chain completeness for two legs | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V34 | broker adapter is available in process for put-chain completeness for two legs | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V35 | environment contains credentials for put-chain completeness for two legs | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V36 | order manager exists in composition for put-chain completeness for two legs | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V37 | risk engine exists in composition for put-chain completeness for two legs | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V38 | trade decision exists downstream for put-chain completeness for two legs | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V39 | serialization round trip for put-chain completeness for two legs | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AI-V40 | schema version incompatible for put-chain completeness for two legs | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AI-ACCEPT-001:** All forty vectors pass without external calls.
**AI-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AI-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AJ — Expiry selection catalog
The vectors below verify shared expiry selection. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AJ-V01 | nominal valid input for shared expiry selection | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V02 | required field absent for shared expiry selection | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V03 | non-finite numeric value for shared expiry selection | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V04 | boundary value equal to minimum for shared expiry selection | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V05 | value one quantum below minimum for shared expiry selection | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V06 | value one quantum above maximum for shared expiry selection | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V07 | timestamp exactly at start boundary for shared expiry selection | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V08 | timestamp exactly at end boundary for shared expiry selection | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V09 | two otherwise equal candidates for shared expiry selection | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V10 | input collection reversed for shared expiry selection | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V11 | unrelated metadata added for shared expiry selection | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V12 | optional metadata absent for shared expiry selection | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V13 | optional metadata malformed for shared expiry selection | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V14 | immutable input reused for shared expiry selection | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V15 | same input evaluated twice for shared expiry selection | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V16 | same input evaluated concurrently for shared expiry selection | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V17 | event sink raises exception for shared expiry selection | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V18 | unknown enum supplied for shared expiry selection | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V19 | unknown payload field supplied for shared expiry selection | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V20 | unsupported underlying supplied for shared expiry selection | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V21 | snapshot id conflicts with contract identity for shared expiry selection | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V22 | selected contract token absent for shared expiry selection | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V23 | symbol absent but immutable instrument id present for shared expiry selection | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V24 | decimal precision exceeds display precision for shared expiry selection | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V25 | negative zero submitted for shared expiry selection | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V26 | large but finite option chain for shared expiry selection | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V27 | duplicate contract id with same facts for shared expiry selection | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V28 | duplicate contract id with conflicting facts for shared expiry selection | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V29 | score framework rejects factor bundle for shared expiry selection | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V30 | score framework returns sealed score for shared expiry selection | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V31 | RiskProfile prefers no entry for shared expiry selection | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V32 | PortfolioSnapshot contains exposure for shared expiry selection | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V33 | HistoricalSeries is injected for shared expiry selection | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V34 | broker adapter is available in process for shared expiry selection | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V35 | environment contains credentials for shared expiry selection | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V36 | order manager exists in composition for shared expiry selection | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V37 | risk engine exists in composition for shared expiry selection | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V38 | trade decision exists downstream for shared expiry selection | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V39 | serialization round trip for shared expiry selection | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AJ-V40 | schema version incompatible for shared expiry selection | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AJ-ACCEPT-001:** All forty vectors pass without external calls.
**AJ-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AJ-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AK — Short-put selection catalog
The vectors below verify short put delta-target selection. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AK-V01 | nominal valid input for short put delta-target selection | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V02 | required field absent for short put delta-target selection | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V03 | non-finite numeric value for short put delta-target selection | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V04 | boundary value equal to minimum for short put delta-target selection | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V05 | value one quantum below minimum for short put delta-target selection | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V06 | value one quantum above maximum for short put delta-target selection | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V07 | timestamp exactly at start boundary for short put delta-target selection | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V08 | timestamp exactly at end boundary for short put delta-target selection | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V09 | two otherwise equal candidates for short put delta-target selection | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V10 | input collection reversed for short put delta-target selection | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V11 | unrelated metadata added for short put delta-target selection | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V12 | optional metadata absent for short put delta-target selection | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V13 | optional metadata malformed for short put delta-target selection | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V14 | immutable input reused for short put delta-target selection | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V15 | same input evaluated twice for short put delta-target selection | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V16 | same input evaluated concurrently for short put delta-target selection | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V17 | event sink raises exception for short put delta-target selection | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V18 | unknown enum supplied for short put delta-target selection | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V19 | unknown payload field supplied for short put delta-target selection | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V20 | unsupported underlying supplied for short put delta-target selection | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V21 | snapshot id conflicts with contract identity for short put delta-target selection | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V22 | selected contract token absent for short put delta-target selection | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V23 | symbol absent but immutable instrument id present for short put delta-target selection | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V24 | decimal precision exceeds display precision for short put delta-target selection | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V25 | negative zero submitted for short put delta-target selection | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V26 | large but finite option chain for short put delta-target selection | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V27 | duplicate contract id with same facts for short put delta-target selection | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V28 | duplicate contract id with conflicting facts for short put delta-target selection | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V29 | score framework rejects factor bundle for short put delta-target selection | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V30 | score framework returns sealed score for short put delta-target selection | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V31 | RiskProfile prefers no entry for short put delta-target selection | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V32 | PortfolioSnapshot contains exposure for short put delta-target selection | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V33 | HistoricalSeries is injected for short put delta-target selection | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V34 | broker adapter is available in process for short put delta-target selection | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V35 | environment contains credentials for short put delta-target selection | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V36 | order manager exists in composition for short put delta-target selection | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V37 | risk engine exists in composition for short put delta-target selection | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V38 | trade decision exists downstream for short put delta-target selection | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V39 | serialization round trip for short put delta-target selection | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AK-V40 | schema version incompatible for short put delta-target selection | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AK-ACCEPT-001:** All forty vectors pass without external calls.
**AK-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AK-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AL — Long-put / wing catalog
The vectors below verify long put further-OTM wing selection. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AL-V01 | nominal valid input for long put further-OTM wing selection | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V02 | required field absent for long put further-OTM wing selection | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V03 | non-finite numeric value for long put further-OTM wing selection | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V04 | boundary value equal to minimum for long put further-OTM wing selection | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V05 | value one quantum below minimum for long put further-OTM wing selection | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V06 | value one quantum above maximum for long put further-OTM wing selection | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V07 | timestamp exactly at start boundary for long put further-OTM wing selection | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V08 | timestamp exactly at end boundary for long put further-OTM wing selection | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V09 | two otherwise equal candidates for long put further-OTM wing selection | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V10 | input collection reversed for long put further-OTM wing selection | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V11 | unrelated metadata added for long put further-OTM wing selection | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V12 | optional metadata absent for long put further-OTM wing selection | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V13 | optional metadata malformed for long put further-OTM wing selection | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V14 | immutable input reused for long put further-OTM wing selection | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V15 | same input evaluated twice for long put further-OTM wing selection | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V16 | same input evaluated concurrently for long put further-OTM wing selection | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V17 | event sink raises exception for long put further-OTM wing selection | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V18 | unknown enum supplied for long put further-OTM wing selection | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V19 | unknown payload field supplied for long put further-OTM wing selection | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V20 | unsupported underlying supplied for long put further-OTM wing selection | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V21 | snapshot id conflicts with contract identity for long put further-OTM wing selection | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V22 | selected contract token absent for long put further-OTM wing selection | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V23 | symbol absent but immutable instrument id present for long put further-OTM wing selection | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V24 | decimal precision exceeds display precision for long put further-OTM wing selection | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V25 | negative zero submitted for long put further-OTM wing selection | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V26 | large but finite option chain for long put further-OTM wing selection | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V27 | duplicate contract id with same facts for long put further-OTM wing selection | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V28 | duplicate contract id with conflicting facts for long put further-OTM wing selection | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V29 | score framework rejects factor bundle for long put further-OTM wing selection | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V30 | score framework returns sealed score for long put further-OTM wing selection | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V31 | RiskProfile prefers no entry for long put further-OTM wing selection | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V32 | PortfolioSnapshot contains exposure for long put further-OTM wing selection | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V33 | HistoricalSeries is injected for long put further-OTM wing selection | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V34 | broker adapter is available in process for long put further-OTM wing selection | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V35 | environment contains credentials for long put further-OTM wing selection | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V36 | order manager exists in composition for long put further-OTM wing selection | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V37 | risk engine exists in composition for long put further-OTM wing selection | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V38 | trade decision exists downstream for long put further-OTM wing selection | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V39 | serialization round trip for long put further-OTM wing selection | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AL-V40 | schema version incompatible for long put further-OTM wing selection | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AL-ACCEPT-001:** All forty vectors pass without external calls.
**AL-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AL-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AM — Structure geometry catalog
The vectors below verify long_put < short_put geometry. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AM-V01 | nominal valid input for long_put < short_put geometry | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V02 | required field absent for long_put < short_put geometry | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V03 | non-finite numeric value for long_put < short_put geometry | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V04 | boundary value equal to minimum for long_put < short_put geometry | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V05 | value one quantum below minimum for long_put < short_put geometry | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V06 | value one quantum above maximum for long_put < short_put geometry | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V07 | timestamp exactly at start boundary for long_put < short_put geometry | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V08 | timestamp exactly at end boundary for long_put < short_put geometry | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V09 | two otherwise equal candidates for long_put < short_put geometry | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V10 | input collection reversed for long_put < short_put geometry | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V11 | unrelated metadata added for long_put < short_put geometry | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V12 | optional metadata absent for long_put < short_put geometry | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V13 | optional metadata malformed for long_put < short_put geometry | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V14 | immutable input reused for long_put < short_put geometry | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V15 | same input evaluated twice for long_put < short_put geometry | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V16 | same input evaluated concurrently for long_put < short_put geometry | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V17 | event sink raises exception for long_put < short_put geometry | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V18 | unknown enum supplied for long_put < short_put geometry | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V19 | unknown payload field supplied for long_put < short_put geometry | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V20 | unsupported underlying supplied for long_put < short_put geometry | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V21 | snapshot id conflicts with contract identity for long_put < short_put geometry | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V22 | selected contract token absent for long_put < short_put geometry | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V23 | symbol absent but immutable instrument id present for long_put < short_put geometry | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V24 | decimal precision exceeds display precision for long_put < short_put geometry | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V25 | negative zero submitted for long_put < short_put geometry | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V26 | large but finite option chain for long_put < short_put geometry | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V27 | duplicate contract id with same facts for long_put < short_put geometry | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V28 | duplicate contract id with conflicting facts for long_put < short_put geometry | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V29 | score framework rejects factor bundle for long_put < short_put geometry | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V30 | score framework returns sealed score for long_put < short_put geometry | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V31 | RiskProfile prefers no entry for long_put < short_put geometry | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V32 | PortfolioSnapshot contains exposure for long_put < short_put geometry | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V33 | HistoricalSeries is injected for long_put < short_put geometry | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V34 | broker adapter is available in process for long_put < short_put geometry | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V35 | environment contains credentials for long_put < short_put geometry | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V36 | order manager exists in composition for long_put < short_put geometry | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V37 | risk engine exists in composition for long_put < short_put geometry | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V38 | trade decision exists downstream for long_put < short_put geometry | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V39 | serialization round trip for long_put < short_put geometry | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AM-V40 | schema version incompatible for long_put < short_put geometry | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AM-ACCEPT-001:** All forty vectors pass without external calls.
**AM-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AM-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AN — Premium and credit catalog
The vectors below verify net credit and minimum premium. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AN-V01 | nominal valid input for net credit and minimum premium | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V02 | required field absent for net credit and minimum premium | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V03 | non-finite numeric value for net credit and minimum premium | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V04 | boundary value equal to minimum for net credit and minimum premium | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V05 | value one quantum below minimum for net credit and minimum premium | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V06 | value one quantum above maximum for net credit and minimum premium | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V07 | timestamp exactly at start boundary for net credit and minimum premium | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V08 | timestamp exactly at end boundary for net credit and minimum premium | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V09 | two otherwise equal candidates for net credit and minimum premium | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V10 | input collection reversed for net credit and minimum premium | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V11 | unrelated metadata added for net credit and minimum premium | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V12 | optional metadata absent for net credit and minimum premium | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V13 | optional metadata malformed for net credit and minimum premium | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V14 | immutable input reused for net credit and minimum premium | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V15 | same input evaluated twice for net credit and minimum premium | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V16 | same input evaluated concurrently for net credit and minimum premium | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V17 | event sink raises exception for net credit and minimum premium | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V18 | unknown enum supplied for net credit and minimum premium | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V19 | unknown payload field supplied for net credit and minimum premium | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V20 | unsupported underlying supplied for net credit and minimum premium | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V21 | snapshot id conflicts with contract identity for net credit and minimum premium | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V22 | selected contract token absent for net credit and minimum premium | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V23 | symbol absent but immutable instrument id present for net credit and minimum premium | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V24 | decimal precision exceeds display precision for net credit and minimum premium | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V25 | negative zero submitted for net credit and minimum premium | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V26 | large but finite option chain for net credit and minimum premium | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V27 | duplicate contract id with same facts for net credit and minimum premium | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V28 | duplicate contract id with conflicting facts for net credit and minimum premium | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V29 | score framework rejects factor bundle for net credit and minimum premium | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V30 | score framework returns sealed score for net credit and minimum premium | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V31 | RiskProfile prefers no entry for net credit and minimum premium | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V32 | PortfolioSnapshot contains exposure for net credit and minimum premium | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V33 | HistoricalSeries is injected for net credit and minimum premium | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V34 | broker adapter is available in process for net credit and minimum premium | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V35 | environment contains credentials for net credit and minimum premium | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V36 | order manager exists in composition for net credit and minimum premium | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V37 | risk engine exists in composition for net credit and minimum premium | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V38 | trade decision exists downstream for net credit and minimum premium | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V39 | serialization round trip for net credit and minimum premium | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AN-V40 | schema version incompatible for net credit and minimum premium | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AN-ACCEPT-001:** All forty vectors pass without external calls.
**AN-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AN-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AO — Max profit / max loss catalog
The vectors below verify defined-risk max profit and max loss. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AO-V01 | nominal valid input for defined-risk max profit and max loss | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V02 | required field absent for defined-risk max profit and max loss | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V03 | non-finite numeric value for defined-risk max profit and max loss | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V04 | boundary value equal to minimum for defined-risk max profit and max loss | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V05 | value one quantum below minimum for defined-risk max profit and max loss | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V06 | value one quantum above maximum for defined-risk max profit and max loss | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V07 | timestamp exactly at start boundary for defined-risk max profit and max loss | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V08 | timestamp exactly at end boundary for defined-risk max profit and max loss | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V09 | two otherwise equal candidates for defined-risk max profit and max loss | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V10 | input collection reversed for defined-risk max profit and max loss | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V11 | unrelated metadata added for defined-risk max profit and max loss | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V12 | optional metadata absent for defined-risk max profit and max loss | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V13 | optional metadata malformed for defined-risk max profit and max loss | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V14 | immutable input reused for defined-risk max profit and max loss | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V15 | same input evaluated twice for defined-risk max profit and max loss | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V16 | same input evaluated concurrently for defined-risk max profit and max loss | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V17 | event sink raises exception for defined-risk max profit and max loss | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V18 | unknown enum supplied for defined-risk max profit and max loss | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V19 | unknown payload field supplied for defined-risk max profit and max loss | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V20 | unsupported underlying supplied for defined-risk max profit and max loss | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V21 | snapshot id conflicts with contract identity for defined-risk max profit and max loss | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V22 | selected contract token absent for defined-risk max profit and max loss | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V23 | symbol absent but immutable instrument id present for defined-risk max profit and max loss | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V24 | decimal precision exceeds display precision for defined-risk max profit and max loss | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V25 | negative zero submitted for defined-risk max profit and max loss | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V26 | large but finite option chain for defined-risk max profit and max loss | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V27 | duplicate contract id with same facts for defined-risk max profit and max loss | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V28 | duplicate contract id with conflicting facts for defined-risk max profit and max loss | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V29 | score framework rejects factor bundle for defined-risk max profit and max loss | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V30 | score framework returns sealed score for defined-risk max profit and max loss | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V31 | RiskProfile prefers no entry for defined-risk max profit and max loss | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V32 | PortfolioSnapshot contains exposure for defined-risk max profit and max loss | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V33 | HistoricalSeries is injected for defined-risk max profit and max loss | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V34 | broker adapter is available in process for defined-risk max profit and max loss | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V35 | environment contains credentials for defined-risk max profit and max loss | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V36 | order manager exists in composition for defined-risk max profit and max loss | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V37 | risk engine exists in composition for defined-risk max profit and max loss | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V38 | trade decision exists downstream for defined-risk max profit and max loss | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V39 | serialization round trip for defined-risk max profit and max loss | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AO-V40 | schema version incompatible for defined-risk max profit and max loss | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AO-ACCEPT-001:** All forty vectors pass without external calls.
**AO-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AO-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AP — POP heuristic catalog
The vectors below verify probability-of-profit heuristic. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AP-V01 | nominal valid input for probability-of-profit heuristic | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V02 | required field absent for probability-of-profit heuristic | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V03 | non-finite numeric value for probability-of-profit heuristic | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V04 | boundary value equal to minimum for probability-of-profit heuristic | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V05 | value one quantum below minimum for probability-of-profit heuristic | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V06 | value one quantum above maximum for probability-of-profit heuristic | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V07 | timestamp exactly at start boundary for probability-of-profit heuristic | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V08 | timestamp exactly at end boundary for probability-of-profit heuristic | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V09 | two otherwise equal candidates for probability-of-profit heuristic | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V10 | input collection reversed for probability-of-profit heuristic | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V11 | unrelated metadata added for probability-of-profit heuristic | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V12 | optional metadata absent for probability-of-profit heuristic | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V13 | optional metadata malformed for probability-of-profit heuristic | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V14 | immutable input reused for probability-of-profit heuristic | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V15 | same input evaluated twice for probability-of-profit heuristic | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V16 | same input evaluated concurrently for probability-of-profit heuristic | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V17 | event sink raises exception for probability-of-profit heuristic | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V18 | unknown enum supplied for probability-of-profit heuristic | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V19 | unknown payload field supplied for probability-of-profit heuristic | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V20 | unsupported underlying supplied for probability-of-profit heuristic | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V21 | snapshot id conflicts with contract identity for probability-of-profit heuristic | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V22 | selected contract token absent for probability-of-profit heuristic | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V23 | symbol absent but immutable instrument id present for probability-of-profit heuristic | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V24 | decimal precision exceeds display precision for probability-of-profit heuristic | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V25 | negative zero submitted for probability-of-profit heuristic | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V26 | large but finite option chain for probability-of-profit heuristic | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V27 | duplicate contract id with same facts for probability-of-profit heuristic | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V28 | duplicate contract id with conflicting facts for probability-of-profit heuristic | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V29 | score framework rejects factor bundle for probability-of-profit heuristic | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V30 | score framework returns sealed score for probability-of-profit heuristic | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V31 | RiskProfile prefers no entry for probability-of-profit heuristic | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V32 | PortfolioSnapshot contains exposure for probability-of-profit heuristic | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V33 | HistoricalSeries is injected for probability-of-profit heuristic | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V34 | broker adapter is available in process for probability-of-profit heuristic | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V35 | environment contains credentials for probability-of-profit heuristic | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V36 | order manager exists in composition for probability-of-profit heuristic | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V37 | risk engine exists in composition for probability-of-profit heuristic | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V38 | trade decision exists downstream for probability-of-profit heuristic | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V39 | serialization round trip for probability-of-profit heuristic | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AP-V40 | schema version incompatible for probability-of-profit heuristic | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AP-ACCEPT-001:** All forty vectors pass without external calls.
**AP-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AP-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AQ — Scoring integration catalog
The vectors below verify PREMIUM_SELLING factor sealing. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AQ-V01 | nominal valid input for PREMIUM_SELLING factor sealing | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V02 | required field absent for PREMIUM_SELLING factor sealing | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V03 | non-finite numeric value for PREMIUM_SELLING factor sealing | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V04 | boundary value equal to minimum for PREMIUM_SELLING factor sealing | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V05 | value one quantum below minimum for PREMIUM_SELLING factor sealing | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V06 | value one quantum above maximum for PREMIUM_SELLING factor sealing | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V07 | timestamp exactly at start boundary for PREMIUM_SELLING factor sealing | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V08 | timestamp exactly at end boundary for PREMIUM_SELLING factor sealing | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V09 | two otherwise equal candidates for PREMIUM_SELLING factor sealing | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V10 | input collection reversed for PREMIUM_SELLING factor sealing | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V11 | unrelated metadata added for PREMIUM_SELLING factor sealing | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V12 | optional metadata absent for PREMIUM_SELLING factor sealing | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V13 | optional metadata malformed for PREMIUM_SELLING factor sealing | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V14 | immutable input reused for PREMIUM_SELLING factor sealing | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V15 | same input evaluated twice for PREMIUM_SELLING factor sealing | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V16 | same input evaluated concurrently for PREMIUM_SELLING factor sealing | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V17 | event sink raises exception for PREMIUM_SELLING factor sealing | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V18 | unknown enum supplied for PREMIUM_SELLING factor sealing | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V19 | unknown payload field supplied for PREMIUM_SELLING factor sealing | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V20 | unsupported underlying supplied for PREMIUM_SELLING factor sealing | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V21 | snapshot id conflicts with contract identity for PREMIUM_SELLING factor sealing | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V22 | selected contract token absent for PREMIUM_SELLING factor sealing | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V23 | symbol absent but immutable instrument id present for PREMIUM_SELLING factor sealing | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V24 | decimal precision exceeds display precision for PREMIUM_SELLING factor sealing | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V25 | negative zero submitted for PREMIUM_SELLING factor sealing | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V26 | large but finite option chain for PREMIUM_SELLING factor sealing | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V27 | duplicate contract id with same facts for PREMIUM_SELLING factor sealing | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V28 | duplicate contract id with conflicting facts for PREMIUM_SELLING factor sealing | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V29 | score framework rejects factor bundle for PREMIUM_SELLING factor sealing | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V30 | score framework returns sealed score for PREMIUM_SELLING factor sealing | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V31 | RiskProfile prefers no entry for PREMIUM_SELLING factor sealing | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V32 | PortfolioSnapshot contains exposure for PREMIUM_SELLING factor sealing | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V33 | HistoricalSeries is injected for PREMIUM_SELLING factor sealing | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V34 | broker adapter is available in process for PREMIUM_SELLING factor sealing | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V35 | environment contains credentials for PREMIUM_SELLING factor sealing | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V36 | order manager exists in composition for PREMIUM_SELLING factor sealing | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V37 | risk engine exists in composition for PREMIUM_SELLING factor sealing | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V38 | trade decision exists downstream for PREMIUM_SELLING factor sealing | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V39 | serialization round trip for PREMIUM_SELLING factor sealing | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AQ-V40 | schema version incompatible for PREMIUM_SELLING factor sealing | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AQ-ACCEPT-001:** All forty vectors pass without external calls.
**AQ-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AQ-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AR — TradingSignal mapping catalog
The vectors below verify ENTER / ABSTAIN / REJECT mapping. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AR-V01 | nominal valid input for ENTER / ABSTAIN / REJECT mapping | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V02 | required field absent for ENTER / ABSTAIN / REJECT mapping | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V03 | non-finite numeric value for ENTER / ABSTAIN / REJECT mapping | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V04 | boundary value equal to minimum for ENTER / ABSTAIN / REJECT mapping | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V05 | value one quantum below minimum for ENTER / ABSTAIN / REJECT mapping | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V06 | value one quantum above maximum for ENTER / ABSTAIN / REJECT mapping | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V07 | timestamp exactly at start boundary for ENTER / ABSTAIN / REJECT mapping | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V08 | timestamp exactly at end boundary for ENTER / ABSTAIN / REJECT mapping | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V09 | two otherwise equal candidates for ENTER / ABSTAIN / REJECT mapping | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V10 | input collection reversed for ENTER / ABSTAIN / REJECT mapping | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V11 | unrelated metadata added for ENTER / ABSTAIN / REJECT mapping | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V12 | optional metadata absent for ENTER / ABSTAIN / REJECT mapping | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V13 | optional metadata malformed for ENTER / ABSTAIN / REJECT mapping | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V14 | immutable input reused for ENTER / ABSTAIN / REJECT mapping | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V15 | same input evaluated twice for ENTER / ABSTAIN / REJECT mapping | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V16 | same input evaluated concurrently for ENTER / ABSTAIN / REJECT mapping | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V17 | event sink raises exception for ENTER / ABSTAIN / REJECT mapping | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V18 | unknown enum supplied for ENTER / ABSTAIN / REJECT mapping | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V19 | unknown payload field supplied for ENTER / ABSTAIN / REJECT mapping | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V20 | unsupported underlying supplied for ENTER / ABSTAIN / REJECT mapping | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V21 | snapshot id conflicts with contract identity for ENTER / ABSTAIN / REJECT mapping | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V22 | selected contract token absent for ENTER / ABSTAIN / REJECT mapping | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V23 | symbol absent but immutable instrument id present for ENTER / ABSTAIN / REJECT mapping | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V24 | decimal precision exceeds display precision for ENTER / ABSTAIN / REJECT mapping | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V25 | negative zero submitted for ENTER / ABSTAIN / REJECT mapping | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V26 | large but finite option chain for ENTER / ABSTAIN / REJECT mapping | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V27 | duplicate contract id with same facts for ENTER / ABSTAIN / REJECT mapping | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V28 | duplicate contract id with conflicting facts for ENTER / ABSTAIN / REJECT mapping | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V29 | score framework rejects factor bundle for ENTER / ABSTAIN / REJECT mapping | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V30 | score framework returns sealed score for ENTER / ABSTAIN / REJECT mapping | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V31 | RiskProfile prefers no entry for ENTER / ABSTAIN / REJECT mapping | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V32 | PortfolioSnapshot contains exposure for ENTER / ABSTAIN / REJECT mapping | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V33 | HistoricalSeries is injected for ENTER / ABSTAIN / REJECT mapping | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V34 | broker adapter is available in process for ENTER / ABSTAIN / REJECT mapping | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V35 | environment contains credentials for ENTER / ABSTAIN / REJECT mapping | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V36 | order manager exists in composition for ENTER / ABSTAIN / REJECT mapping | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V37 | risk engine exists in composition for ENTER / ABSTAIN / REJECT mapping | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V38 | trade decision exists downstream for ENTER / ABSTAIN / REJECT mapping | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V39 | serialization round trip for ENTER / ABSTAIN / REJECT mapping | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AR-V40 | schema version incompatible for ENTER / ABSTAIN / REJECT mapping | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AR-ACCEPT-001:** All forty vectors pass without external calls.
**AR-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AR-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AS — Serialization catalog
The vectors below verify versioned canonical JSON. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AS-V01 | nominal valid input for versioned canonical JSON | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V02 | required field absent for versioned canonical JSON | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V03 | non-finite numeric value for versioned canonical JSON | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V04 | boundary value equal to minimum for versioned canonical JSON | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V05 | value one quantum below minimum for versioned canonical JSON | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V06 | value one quantum above maximum for versioned canonical JSON | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V07 | timestamp exactly at start boundary for versioned canonical JSON | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V08 | timestamp exactly at end boundary for versioned canonical JSON | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V09 | two otherwise equal candidates for versioned canonical JSON | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V10 | input collection reversed for versioned canonical JSON | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V11 | unrelated metadata added for versioned canonical JSON | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V12 | optional metadata absent for versioned canonical JSON | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V13 | optional metadata malformed for versioned canonical JSON | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V14 | immutable input reused for versioned canonical JSON | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V15 | same input evaluated twice for versioned canonical JSON | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V16 | same input evaluated concurrently for versioned canonical JSON | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V17 | event sink raises exception for versioned canonical JSON | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V18 | unknown enum supplied for versioned canonical JSON | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V19 | unknown payload field supplied for versioned canonical JSON | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V20 | unsupported underlying supplied for versioned canonical JSON | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V21 | snapshot id conflicts with contract identity for versioned canonical JSON | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V22 | selected contract token absent for versioned canonical JSON | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V23 | symbol absent but immutable instrument id present for versioned canonical JSON | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V24 | decimal precision exceeds display precision for versioned canonical JSON | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V25 | negative zero submitted for versioned canonical JSON | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V26 | large but finite option chain for versioned canonical JSON | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V27 | duplicate contract id with same facts for versioned canonical JSON | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V28 | duplicate contract id with conflicting facts for versioned canonical JSON | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V29 | score framework rejects factor bundle for versioned canonical JSON | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V30 | score framework returns sealed score for versioned canonical JSON | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V31 | RiskProfile prefers no entry for versioned canonical JSON | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V32 | PortfolioSnapshot contains exposure for versioned canonical JSON | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V33 | HistoricalSeries is injected for versioned canonical JSON | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V34 | broker adapter is available in process for versioned canonical JSON | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V35 | environment contains credentials for versioned canonical JSON | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V36 | order manager exists in composition for versioned canonical JSON | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V37 | risk engine exists in composition for versioned canonical JSON | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V38 | trade decision exists downstream for versioned canonical JSON | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V39 | serialization round trip for versioned canonical JSON | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AS-V40 | schema version incompatible for versioned canonical JSON | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AS-ACCEPT-001:** All forty vectors pass without external calls.
**AS-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AS-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AT — Concurrency catalog
The vectors below verify thread-safety and isolation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AT-V01 | nominal valid input for thread-safety and isolation | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V02 | required field absent for thread-safety and isolation | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V03 | non-finite numeric value for thread-safety and isolation | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V04 | boundary value equal to minimum for thread-safety and isolation | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V05 | value one quantum below minimum for thread-safety and isolation | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V06 | value one quantum above maximum for thread-safety and isolation | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V07 | timestamp exactly at start boundary for thread-safety and isolation | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V08 | timestamp exactly at end boundary for thread-safety and isolation | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V09 | two otherwise equal candidates for thread-safety and isolation | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V10 | input collection reversed for thread-safety and isolation | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V11 | unrelated metadata added for thread-safety and isolation | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V12 | optional metadata absent for thread-safety and isolation | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V13 | optional metadata malformed for thread-safety and isolation | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V14 | immutable input reused for thread-safety and isolation | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V15 | same input evaluated twice for thread-safety and isolation | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V16 | same input evaluated concurrently for thread-safety and isolation | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V17 | event sink raises exception for thread-safety and isolation | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V18 | unknown enum supplied for thread-safety and isolation | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V19 | unknown payload field supplied for thread-safety and isolation | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V20 | unsupported underlying supplied for thread-safety and isolation | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V21 | snapshot id conflicts with contract identity for thread-safety and isolation | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V22 | selected contract token absent for thread-safety and isolation | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V23 | symbol absent but immutable instrument id present for thread-safety and isolation | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V24 | decimal precision exceeds display precision for thread-safety and isolation | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V25 | negative zero submitted for thread-safety and isolation | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V26 | large but finite option chain for thread-safety and isolation | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V27 | duplicate contract id with same facts for thread-safety and isolation | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V28 | duplicate contract id with conflicting facts for thread-safety and isolation | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V29 | score framework rejects factor bundle for thread-safety and isolation | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V30 | score framework returns sealed score for thread-safety and isolation | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V31 | RiskProfile prefers no entry for thread-safety and isolation | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V32 | PortfolioSnapshot contains exposure for thread-safety and isolation | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V33 | HistoricalSeries is injected for thread-safety and isolation | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V34 | broker adapter is available in process for thread-safety and isolation | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V35 | environment contains credentials for thread-safety and isolation | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V36 | order manager exists in composition for thread-safety and isolation | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V37 | risk engine exists in composition for thread-safety and isolation | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V38 | trade decision exists downstream for thread-safety and isolation | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V39 | serialization round trip for thread-safety and isolation | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AT-V40 | schema version incompatible for thread-safety and isolation | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AT-ACCEPT-001:** All forty vectors pass without external calls.
**AT-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AT-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AU — Boundary enforcement catalog
The vectors below verify no order, position, or portfolio-risk actions. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AU-V01 | nominal valid input for no order, position, or portfolio-risk actions | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V02 | required field absent for no order, position, or portfolio-risk actions | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V03 | non-finite numeric value for no order, position, or portfolio-risk actions | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V04 | boundary value equal to minimum for no order, position, or portfolio-risk actions | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V05 | value one quantum below minimum for no order, position, or portfolio-risk actions | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V06 | value one quantum above maximum for no order, position, or portfolio-risk actions | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V07 | timestamp exactly at start boundary for no order, position, or portfolio-risk actions | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V08 | timestamp exactly at end boundary for no order, position, or portfolio-risk actions | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V09 | two otherwise equal candidates for no order, position, or portfolio-risk actions | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V10 | input collection reversed for no order, position, or portfolio-risk actions | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V11 | unrelated metadata added for no order, position, or portfolio-risk actions | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V12 | optional metadata absent for no order, position, or portfolio-risk actions | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V13 | optional metadata malformed for no order, position, or portfolio-risk actions | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V14 | immutable input reused for no order, position, or portfolio-risk actions | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V15 | same input evaluated twice for no order, position, or portfolio-risk actions | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V16 | same input evaluated concurrently for no order, position, or portfolio-risk actions | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V17 | event sink raises exception for no order, position, or portfolio-risk actions | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V18 | unknown enum supplied for no order, position, or portfolio-risk actions | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V19 | unknown payload field supplied for no order, position, or portfolio-risk actions | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V20 | unsupported underlying supplied for no order, position, or portfolio-risk actions | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V21 | snapshot id conflicts with contract identity for no order, position, or portfolio-risk actions | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V22 | selected contract token absent for no order, position, or portfolio-risk actions | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V23 | symbol absent but immutable instrument id present for no order, position, or portfolio-risk actions | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V24 | decimal precision exceeds display precision for no order, position, or portfolio-risk actions | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V25 | negative zero submitted for no order, position, or portfolio-risk actions | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V26 | large but finite option chain for no order, position, or portfolio-risk actions | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V27 | duplicate contract id with same facts for no order, position, or portfolio-risk actions | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V28 | duplicate contract id with conflicting facts for no order, position, or portfolio-risk actions | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V29 | score framework rejects factor bundle for no order, position, or portfolio-risk actions | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V30 | score framework returns sealed score for no order, position, or portfolio-risk actions | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V31 | RiskProfile prefers no entry for no order, position, or portfolio-risk actions | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V32 | PortfolioSnapshot contains exposure for no order, position, or portfolio-risk actions | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V33 | HistoricalSeries is injected for no order, position, or portfolio-risk actions | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V34 | broker adapter is available in process for no order, position, or portfolio-risk actions | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V35 | environment contains credentials for no order, position, or portfolio-risk actions | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V36 | order manager exists in composition for no order, position, or portfolio-risk actions | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V37 | risk engine exists in composition for no order, position, or portfolio-risk actions | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V38 | trade decision exists downstream for no order, position, or portfolio-risk actions | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V39 | serialization round trip for no order, position, or portfolio-risk actions | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AU-V40 | schema version incompatible for no order, position, or portfolio-risk actions | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AU-ACCEPT-001:** All forty vectors pass without external calls.
**AU-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AU-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AV — Event-sink catalog
The vectors below verify optional observational events. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AV-V01 | nominal valid input for optional observational events | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V02 | required field absent for optional observational events | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V03 | non-finite numeric value for optional observational events | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V04 | boundary value equal to minimum for optional observational events | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V05 | value one quantum below minimum for optional observational events | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V06 | value one quantum above maximum for optional observational events | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V07 | timestamp exactly at start boundary for optional observational events | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V08 | timestamp exactly at end boundary for optional observational events | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V09 | two otherwise equal candidates for optional observational events | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V10 | input collection reversed for optional observational events | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V11 | unrelated metadata added for optional observational events | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V12 | optional metadata absent for optional observational events | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V13 | optional metadata malformed for optional observational events | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V14 | immutable input reused for optional observational events | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V15 | same input evaluated twice for optional observational events | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V16 | same input evaluated concurrently for optional observational events | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V17 | event sink raises exception for optional observational events | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V18 | unknown enum supplied for optional observational events | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V19 | unknown payload field supplied for optional observational events | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V20 | unsupported underlying supplied for optional observational events | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V21 | snapshot id conflicts with contract identity for optional observational events | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V22 | selected contract token absent for optional observational events | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V23 | symbol absent but immutable instrument id present for optional observational events | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V24 | decimal precision exceeds display precision for optional observational events | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V25 | negative zero submitted for optional observational events | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V26 | large but finite option chain for optional observational events | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V27 | duplicate contract id with same facts for optional observational events | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V28 | duplicate contract id with conflicting facts for optional observational events | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V29 | score framework rejects factor bundle for optional observational events | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V30 | score framework returns sealed score for optional observational events | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V31 | RiskProfile prefers no entry for optional observational events | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V32 | PortfolioSnapshot contains exposure for optional observational events | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V33 | HistoricalSeries is injected for optional observational events | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V34 | broker adapter is available in process for optional observational events | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V35 | environment contains credentials for optional observational events | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V36 | order manager exists in composition for optional observational events | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V37 | risk engine exists in composition for optional observational events | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V38 | trade decision exists downstream for optional observational events | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V39 | serialization round trip for optional observational events | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AV-V40 | schema version incompatible for optional observational events | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AV-ACCEPT-001:** All forty vectors pass without external calls.
**AV-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AV-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AW — Unsupported-underlying catalog
The vectors below verify underlying allow-list. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AW-V01 | nominal valid input for underlying allow-list | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V02 | required field absent for underlying allow-list | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V03 | non-finite numeric value for underlying allow-list | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V04 | boundary value equal to minimum for underlying allow-list | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V05 | value one quantum below minimum for underlying allow-list | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V06 | value one quantum above maximum for underlying allow-list | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V07 | timestamp exactly at start boundary for underlying allow-list | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V08 | timestamp exactly at end boundary for underlying allow-list | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V09 | two otherwise equal candidates for underlying allow-list | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V10 | input collection reversed for underlying allow-list | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V11 | unrelated metadata added for underlying allow-list | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V12 | optional metadata absent for underlying allow-list | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V13 | optional metadata malformed for underlying allow-list | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V14 | immutable input reused for underlying allow-list | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V15 | same input evaluated twice for underlying allow-list | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V16 | same input evaluated concurrently for underlying allow-list | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V17 | event sink raises exception for underlying allow-list | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V18 | unknown enum supplied for underlying allow-list | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V19 | unknown payload field supplied for underlying allow-list | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V20 | unsupported underlying supplied for underlying allow-list | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V21 | snapshot id conflicts with contract identity for underlying allow-list | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V22 | selected contract token absent for underlying allow-list | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V23 | symbol absent but immutable instrument id present for underlying allow-list | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V24 | decimal precision exceeds display precision for underlying allow-list | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V25 | negative zero submitted for underlying allow-list | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V26 | large but finite option chain for underlying allow-list | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V27 | duplicate contract id with same facts for underlying allow-list | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V28 | duplicate contract id with conflicting facts for underlying allow-list | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V29 | score framework rejects factor bundle for underlying allow-list | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V30 | score framework returns sealed score for underlying allow-list | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V31 | RiskProfile prefers no entry for underlying allow-list | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V32 | PortfolioSnapshot contains exposure for underlying allow-list | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V33 | HistoricalSeries is injected for underlying allow-list | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V34 | broker adapter is available in process for underlying allow-list | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V35 | environment contains credentials for underlying allow-list | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V36 | order manager exists in composition for underlying allow-list | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V37 | risk engine exists in composition for underlying allow-list | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V38 | trade decision exists downstream for underlying allow-list | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V39 | serialization round trip for underlying allow-list | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AW-V40 | schema version incompatible for underlying allow-list | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AW-ACCEPT-001:** All forty vectors pass without external calls.
**AW-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AW-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AX — Greeks requirement catalog
The vectors below verify required put-delta presence. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AX-V01 | nominal valid input for required put-delta presence | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V02 | required field absent for required put-delta presence | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V03 | non-finite numeric value for required put-delta presence | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V04 | boundary value equal to minimum for required put-delta presence | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V05 | value one quantum below minimum for required put-delta presence | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V06 | value one quantum above maximum for required put-delta presence | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V07 | timestamp exactly at start boundary for required put-delta presence | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V08 | timestamp exactly at end boundary for required put-delta presence | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V09 | two otherwise equal candidates for required put-delta presence | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V10 | input collection reversed for required put-delta presence | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V11 | unrelated metadata added for required put-delta presence | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V12 | optional metadata absent for required put-delta presence | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V13 | optional metadata malformed for required put-delta presence | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V14 | immutable input reused for required put-delta presence | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V15 | same input evaluated twice for required put-delta presence | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V16 | same input evaluated concurrently for required put-delta presence | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V17 | event sink raises exception for required put-delta presence | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V18 | unknown enum supplied for required put-delta presence | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V19 | unknown payload field supplied for required put-delta presence | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V20 | unsupported underlying supplied for required put-delta presence | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V21 | snapshot id conflicts with contract identity for required put-delta presence | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V22 | selected contract token absent for required put-delta presence | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V23 | symbol absent but immutable instrument id present for required put-delta presence | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V24 | decimal precision exceeds display precision for required put-delta presence | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V25 | negative zero submitted for required put-delta presence | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V26 | large but finite option chain for required put-delta presence | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V27 | duplicate contract id with same facts for required put-delta presence | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V28 | duplicate contract id with conflicting facts for required put-delta presence | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V29 | score framework rejects factor bundle for required put-delta presence | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V30 | score framework returns sealed score for required put-delta presence | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V31 | RiskProfile prefers no entry for required put-delta presence | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V32 | PortfolioSnapshot contains exposure for required put-delta presence | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V33 | HistoricalSeries is injected for required put-delta presence | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V34 | broker adapter is available in process for required put-delta presence | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V35 | environment contains credentials for required put-delta presence | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V36 | order manager exists in composition for required put-delta presence | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V37 | risk engine exists in composition for required put-delta presence | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V38 | trade decision exists downstream for required put-delta presence | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V39 | serialization round trip for required put-delta presence | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AX-V40 | schema version incompatible for required put-delta presence | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AX-ACCEPT-001:** All forty vectors pass without external calls.
**AX-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AX-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AY — Wing-width constraint catalog
The vectors below verify configured put-wing width bounds. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AY-V01 | nominal valid input for configured put-wing width bounds | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V02 | required field absent for configured put-wing width bounds | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V03 | non-finite numeric value for configured put-wing width bounds | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V04 | boundary value equal to minimum for configured put-wing width bounds | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V05 | value one quantum below minimum for configured put-wing width bounds | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V06 | value one quantum above maximum for configured put-wing width bounds | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V07 | timestamp exactly at start boundary for configured put-wing width bounds | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V08 | timestamp exactly at end boundary for configured put-wing width bounds | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V09 | two otherwise equal candidates for configured put-wing width bounds | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V10 | input collection reversed for configured put-wing width bounds | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V11 | unrelated metadata added for configured put-wing width bounds | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V12 | optional metadata absent for configured put-wing width bounds | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V13 | optional metadata malformed for configured put-wing width bounds | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V14 | immutable input reused for configured put-wing width bounds | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V15 | same input evaluated twice for configured put-wing width bounds | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V16 | same input evaluated concurrently for configured put-wing width bounds | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V17 | event sink raises exception for configured put-wing width bounds | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V18 | unknown enum supplied for configured put-wing width bounds | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V19 | unknown payload field supplied for configured put-wing width bounds | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V20 | unsupported underlying supplied for configured put-wing width bounds | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V21 | snapshot id conflicts with contract identity for configured put-wing width bounds | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V22 | selected contract token absent for configured put-wing width bounds | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V23 | symbol absent but immutable instrument id present for configured put-wing width bounds | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V24 | decimal precision exceeds display precision for configured put-wing width bounds | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V25 | negative zero submitted for configured put-wing width bounds | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V26 | large but finite option chain for configured put-wing width bounds | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V27 | duplicate contract id with same facts for configured put-wing width bounds | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V28 | duplicate contract id with conflicting facts for configured put-wing width bounds | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V29 | score framework rejects factor bundle for configured put-wing width bounds | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V30 | score framework returns sealed score for configured put-wing width bounds | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V31 | RiskProfile prefers no entry for configured put-wing width bounds | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V32 | PortfolioSnapshot contains exposure for configured put-wing width bounds | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V33 | HistoricalSeries is injected for configured put-wing width bounds | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V34 | broker adapter is available in process for configured put-wing width bounds | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V35 | environment contains credentials for configured put-wing width bounds | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V36 | order manager exists in composition for configured put-wing width bounds | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V37 | risk engine exists in composition for configured put-wing width bounds | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V38 | trade decision exists downstream for configured put-wing width bounds | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V39 | serialization round trip for configured put-wing width bounds | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AY-V40 | schema version incompatible for configured put-wing width bounds | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AY-ACCEPT-001:** All forty vectors pass without external calls.
**AY-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AY-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AZ — Breakeven catalog
The vectors below verify informational lower breakeven derivation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AZ-V01 | nominal valid input for informational lower breakeven derivation | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V02 | required field absent for informational lower breakeven derivation | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V03 | non-finite numeric value for informational lower breakeven derivation | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V04 | boundary value equal to minimum for informational lower breakeven derivation | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V05 | value one quantum below minimum for informational lower breakeven derivation | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V06 | value one quantum above maximum for informational lower breakeven derivation | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V07 | timestamp exactly at start boundary for informational lower breakeven derivation | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V08 | timestamp exactly at end boundary for informational lower breakeven derivation | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V09 | two otherwise equal candidates for informational lower breakeven derivation | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V10 | input collection reversed for informational lower breakeven derivation | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V11 | unrelated metadata added for informational lower breakeven derivation | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V12 | optional metadata absent for informational lower breakeven derivation | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V13 | optional metadata malformed for informational lower breakeven derivation | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V14 | immutable input reused for informational lower breakeven derivation | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V15 | same input evaluated twice for informational lower breakeven derivation | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V16 | same input evaluated concurrently for informational lower breakeven derivation | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V17 | event sink raises exception for informational lower breakeven derivation | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V18 | unknown enum supplied for informational lower breakeven derivation | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V19 | unknown payload field supplied for informational lower breakeven derivation | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V20 | unsupported underlying supplied for informational lower breakeven derivation | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V21 | snapshot id conflicts with contract identity for informational lower breakeven derivation | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V22 | selected contract token absent for informational lower breakeven derivation | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V23 | symbol absent but immutable instrument id present for informational lower breakeven derivation | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V24 | decimal precision exceeds display precision for informational lower breakeven derivation | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V25 | negative zero submitted for informational lower breakeven derivation | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V26 | large but finite option chain for informational lower breakeven derivation | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V27 | duplicate contract id with same facts for informational lower breakeven derivation | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V28 | duplicate contract id with conflicting facts for informational lower breakeven derivation | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V29 | score framework rejects factor bundle for informational lower breakeven derivation | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V30 | score framework returns sealed score for informational lower breakeven derivation | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V31 | RiskProfile prefers no entry for informational lower breakeven derivation | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V32 | PortfolioSnapshot contains exposure for informational lower breakeven derivation | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V33 | HistoricalSeries is injected for informational lower breakeven derivation | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V34 | broker adapter is available in process for informational lower breakeven derivation | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V35 | environment contains credentials for informational lower breakeven derivation | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V36 | order manager exists in composition for informational lower breakeven derivation | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V37 | risk engine exists in composition for informational lower breakeven derivation | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V38 | trade decision exists downstream for informational lower breakeven derivation | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V39 | serialization round trip for informational lower breakeven derivation | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| AZ-V40 | schema version incompatible for informational lower breakeven derivation | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**AZ-ACCEPT-001:** All forty vectors pass without external calls.
**AZ-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AZ-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix BA — Risk-profile hint catalog
The vectors below verify DEFINED risk labeling. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| BA-V01 | nominal valid input for DEFINED risk labeling | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V02 | required field absent for DEFINED risk labeling | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V03 | non-finite numeric value for DEFINED risk labeling | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V04 | boundary value equal to minimum for DEFINED risk labeling | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V05 | value one quantum below minimum for DEFINED risk labeling | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V06 | value one quantum above maximum for DEFINED risk labeling | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V07 | timestamp exactly at start boundary for DEFINED risk labeling | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V08 | timestamp exactly at end boundary for DEFINED risk labeling | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V09 | two otherwise equal candidates for DEFINED risk labeling | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V10 | input collection reversed for DEFINED risk labeling | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V11 | unrelated metadata added for DEFINED risk labeling | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V12 | optional metadata absent for DEFINED risk labeling | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V13 | optional metadata malformed for DEFINED risk labeling | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V14 | immutable input reused for DEFINED risk labeling | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V15 | same input evaluated twice for DEFINED risk labeling | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V16 | same input evaluated concurrently for DEFINED risk labeling | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V17 | event sink raises exception for DEFINED risk labeling | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V18 | unknown enum supplied for DEFINED risk labeling | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V19 | unknown payload field supplied for DEFINED risk labeling | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V20 | unsupported underlying supplied for DEFINED risk labeling | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V21 | snapshot id conflicts with contract identity for DEFINED risk labeling | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V22 | selected contract token absent for DEFINED risk labeling | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V23 | symbol absent but immutable instrument id present for DEFINED risk labeling | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V24 | decimal precision exceeds display precision for DEFINED risk labeling | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V25 | negative zero submitted for DEFINED risk labeling | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V26 | large but finite option chain for DEFINED risk labeling | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V27 | duplicate contract id with same facts for DEFINED risk labeling | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V28 | duplicate contract id with conflicting facts for DEFINED risk labeling | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V29 | score framework rejects factor bundle for DEFINED risk labeling | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V30 | score framework returns sealed score for DEFINED risk labeling | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V31 | RiskProfile prefers no entry for DEFINED risk labeling | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V32 | PortfolioSnapshot contains exposure for DEFINED risk labeling | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V33 | HistoricalSeries is injected for DEFINED risk labeling | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V34 | broker adapter is available in process for DEFINED risk labeling | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V35 | environment contains credentials for DEFINED risk labeling | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V36 | order manager exists in composition for DEFINED risk labeling | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V37 | risk engine exists in composition for DEFINED risk labeling | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V38 | trade decision exists downstream for DEFINED risk labeling | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V39 | serialization round trip for DEFINED risk labeling | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BA-V40 | schema version incompatible for DEFINED risk labeling | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**BA-ACCEPT-001:** All forty vectors pass without external calls.
**BA-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**BA-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix BB — Portfolio snapshot catalog
The vectors below verify informational portfolio preservation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| BB-V01 | nominal valid input for informational portfolio preservation | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V02 | required field absent for informational portfolio preservation | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V03 | non-finite numeric value for informational portfolio preservation | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V04 | boundary value equal to minimum for informational portfolio preservation | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V05 | value one quantum below minimum for informational portfolio preservation | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V06 | value one quantum above maximum for informational portfolio preservation | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V07 | timestamp exactly at start boundary for informational portfolio preservation | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V08 | timestamp exactly at end boundary for informational portfolio preservation | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V09 | two otherwise equal candidates for informational portfolio preservation | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V10 | input collection reversed for informational portfolio preservation | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V11 | unrelated metadata added for informational portfolio preservation | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V12 | optional metadata absent for informational portfolio preservation | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V13 | optional metadata malformed for informational portfolio preservation | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V14 | immutable input reused for informational portfolio preservation | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V15 | same input evaluated twice for informational portfolio preservation | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V16 | same input evaluated concurrently for informational portfolio preservation | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V17 | event sink raises exception for informational portfolio preservation | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V18 | unknown enum supplied for informational portfolio preservation | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V19 | unknown payload field supplied for informational portfolio preservation | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V20 | unsupported underlying supplied for informational portfolio preservation | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V21 | snapshot id conflicts with contract identity for informational portfolio preservation | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V22 | selected contract token absent for informational portfolio preservation | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V23 | symbol absent but immutable instrument id present for informational portfolio preservation | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V24 | decimal precision exceeds display precision for informational portfolio preservation | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V25 | negative zero submitted for informational portfolio preservation | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V26 | large but finite option chain for informational portfolio preservation | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V27 | duplicate contract id with same facts for informational portfolio preservation | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V28 | duplicate contract id with conflicting facts for informational portfolio preservation | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V29 | score framework rejects factor bundle for informational portfolio preservation | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V30 | score framework returns sealed score for informational portfolio preservation | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V31 | RiskProfile prefers no entry for informational portfolio preservation | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V32 | PortfolioSnapshot contains exposure for informational portfolio preservation | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V33 | HistoricalSeries is injected for informational portfolio preservation | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V34 | broker adapter is available in process for informational portfolio preservation | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V35 | environment contains credentials for informational portfolio preservation | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V36 | order manager exists in composition for informational portfolio preservation | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V37 | risk engine exists in composition for informational portfolio preservation | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V38 | trade decision exists downstream for informational portfolio preservation | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V39 | serialization round trip for informational portfolio preservation | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BB-V40 | schema version incompatible for informational portfolio preservation | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**BB-ACCEPT-001:** All forty vectors pass without external calls.
**BB-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**BB-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix BC — Evaluation-engine compatibility catalog
The vectors below verify BaseStrategy.run contract. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| BC-V01 | nominal valid input for BaseStrategy.run contract | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V02 | required field absent for BaseStrategy.run contract | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V03 | non-finite numeric value for BaseStrategy.run contract | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V04 | boundary value equal to minimum for BaseStrategy.run contract | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V05 | value one quantum below minimum for BaseStrategy.run contract | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V06 | value one quantum above maximum for BaseStrategy.run contract | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V07 | timestamp exactly at start boundary for BaseStrategy.run contract | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V08 | timestamp exactly at end boundary for BaseStrategy.run contract | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V09 | two otherwise equal candidates for BaseStrategy.run contract | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V10 | input collection reversed for BaseStrategy.run contract | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V11 | unrelated metadata added for BaseStrategy.run contract | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V12 | optional metadata absent for BaseStrategy.run contract | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V13 | optional metadata malformed for BaseStrategy.run contract | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V14 | immutable input reused for BaseStrategy.run contract | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V15 | same input evaluated twice for BaseStrategy.run contract | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V16 | same input evaluated concurrently for BaseStrategy.run contract | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V17 | event sink raises exception for BaseStrategy.run contract | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V18 | unknown enum supplied for BaseStrategy.run contract | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V19 | unknown payload field supplied for BaseStrategy.run contract | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V20 | unsupported underlying supplied for BaseStrategy.run contract | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V21 | snapshot id conflicts with contract identity for BaseStrategy.run contract | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V22 | selected contract token absent for BaseStrategy.run contract | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V23 | symbol absent but immutable instrument id present for BaseStrategy.run contract | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V24 | decimal precision exceeds display precision for BaseStrategy.run contract | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V25 | negative zero submitted for BaseStrategy.run contract | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V26 | large but finite option chain for BaseStrategy.run contract | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V27 | duplicate contract id with same facts for BaseStrategy.run contract | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V28 | duplicate contract id with conflicting facts for BaseStrategy.run contract | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V29 | score framework rejects factor bundle for BaseStrategy.run contract | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V30 | score framework returns sealed score for BaseStrategy.run contract | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V31 | RiskProfile prefers no entry for BaseStrategy.run contract | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V32 | PortfolioSnapshot contains exposure for BaseStrategy.run contract | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V33 | HistoricalSeries is injected for BaseStrategy.run contract | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V34 | broker adapter is available in process for BaseStrategy.run contract | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V35 | environment contains credentials for BaseStrategy.run contract | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V36 | order manager exists in composition for BaseStrategy.run contract | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V37 | risk engine exists in composition for BaseStrategy.run contract | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V38 | trade decision exists downstream for BaseStrategy.run contract | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V39 | serialization round trip for BaseStrategy.run contract | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BC-V40 | schema version incompatible for BaseStrategy.run contract | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**BC-ACCEPT-001:** All forty vectors pass without external calls.
**BC-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**BC-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix BD — Performance catalog
The vectors below verify complexity and benchmark acceptance vectors. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| BD-V01 | nominal valid input for complexity and benchmark acceptance vectors | expected pass and sealed evidence; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V02 | required field absent for complexity and benchmark acceptance vectors | expected reject with the documented BPS code; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V03 | non-finite numeric value for complexity and benchmark acceptance vectors | expected reject before scoring; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V04 | boundary value equal to minimum for complexity and benchmark acceptance vectors | expected inclusive pass where specified; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V05 | value one quantum below minimum for complexity and benchmark acceptance vectors | expected abstain where the condition is suitability; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V06 | value one quantum above maximum for complexity and benchmark acceptance vectors | expected abstain or reject under the named rule; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V07 | timestamp exactly at start boundary for complexity and benchmark acceptance vectors | expected entry-window inclusion; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V08 | timestamp exactly at end boundary for complexity and benchmark acceptance vectors | expected entry-window abstention; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V09 | two otherwise equal candidates for complexity and benchmark acceptance vectors | expected documented lexical tie-break; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V10 | input collection reversed for complexity and benchmark acceptance vectors | expected byte-equivalent result; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V11 | unrelated metadata added for complexity and benchmark acceptance vectors | expected unchanged selection and score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V12 | optional metadata absent for complexity and benchmark acceptance vectors | expected documented fallback or no-op; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V13 | optional metadata malformed for complexity and benchmark acceptance vectors | expected safe reject without external access; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V14 | immutable input reused for complexity and benchmark acceptance vectors | expected no mutation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V15 | same input evaluated twice for complexity and benchmark acceptance vectors | expected identical canonical JSON; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V16 | same input evaluated concurrently for complexity and benchmark acceptance vectors | expected isolated immutable results; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V17 | event sink raises exception for complexity and benchmark acceptance vectors | expected sealed result and isolated sink failure; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V18 | unknown enum supplied for complexity and benchmark acceptance vectors | expected reject during validation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V19 | unknown payload field supplied for complexity and benchmark acceptance vectors | expected reader-policy behavior; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V20 | unsupported underlying supplied for complexity and benchmark acceptance vectors | expected explicit rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V21 | snapshot id conflicts with contract identity for complexity and benchmark acceptance vectors | expected reject before selection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V22 | selected contract token absent for complexity and benchmark acceptance vectors | expected valid recommendation with null token; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V23 | symbol absent but immutable instrument id present for complexity and benchmark acceptance vectors | expected valid recommendation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V24 | decimal precision exceeds display precision for complexity and benchmark acceptance vectors | expected deterministic sealing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V25 | negative zero submitted for complexity and benchmark acceptance vectors | expected normalization or strict rejection by field contract; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V26 | large but finite option chain for complexity and benchmark acceptance vectors | expected bounded deterministic processing; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V27 | duplicate contract id with same facts for complexity and benchmark acceptance vectors | expected deterministic deduplication policy; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V28 | duplicate contract id with conflicting facts for complexity and benchmark acceptance vectors | expected chain rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V29 | score framework rejects factor bundle for complexity and benchmark acceptance vectors | expected BPS.SCORING.FAILED reject; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V30 | score framework returns sealed score for complexity and benchmark acceptance vectors | expected embedded immutable score; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V31 | RiskProfile prefers no entry for complexity and benchmark acceptance vectors | expected informational preservation, not enforcement; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V32 | PortfolioSnapshot contains exposure for complexity and benchmark acceptance vectors | expected no portfolio mutation or calculation; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V33 | HistoricalSeries is injected for complexity and benchmark acceptance vectors | expected no data fetch and explicit provenance; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V34 | broker adapter is available in process for complexity and benchmark acceptance vectors | expected strategy not to call it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V35 | environment contains credentials for complexity and benchmark acceptance vectors | expected strategy not to read them; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V36 | order manager exists in composition for complexity and benchmark acceptance vectors | expected strategy not to reference it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V37 | risk engine exists in composition for complexity and benchmark acceptance vectors | expected strategy not to invoke it; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V38 | trade decision exists downstream for complexity and benchmark acceptance vectors | expected strategy not to self-select; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V39 | serialization round trip for complexity and benchmark acceptance vectors | expected invariant-preserving reconstructed model; preserve BOUNDARY-BPS and deterministic audit reasons. |
| BD-V40 | schema version incompatible for complexity and benchmark acceptance vectors | expected deserialization rejection; preserve BOUNDARY-BPS and deterministic audit reasons. |

**BD-ACCEPT-001:** All forty vectors pass without external calls.
**BD-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**BD-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.
