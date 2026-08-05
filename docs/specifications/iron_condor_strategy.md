# Iron Condor Strategy — Software Engineering Specification

| Field | Value |
|---|---|
| Module | `strategy/iron_condor_strategy.py` |
| Document version | `1.0.0` |
| Status | Implementation contract |
| Owner | THETA AI TRADER Core Platform |
| Last updated | 2026-08-05 |
| Strategy identifier | `iron_condor` |
| Strategy family | `iron_condor` |
| Risk profile | Defined-risk premium selling; finite wing-capped loss |

---

## 1. Purpose

`strategy/iron_condor_strategy.py` is the deterministic, read-only Iron Condor
strategy plugin for THETA AI TRADER v1.0.

It answers the following bounded question:

> Given an injected `MarketSnapshot`, optional historical and portfolio
> context, an optional risk-profile preference, and an immutable
> `IronCondorConfiguration`, is a four-leg iron condor suitable now; which
> short and long call and put strikes are candidates; what are its estimated
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
| This module | Evaluates iron-condor suitability and emits an immutable signal and recommendation. |
| `strategy/strategy_scoring_framework.py` | Seals normalized factor inputs into `StrategyScore`, `ConfidenceReport`, and `StrategyExplanation`. |
| Trade Decision Engine | Selects among evaluation reports and independently approves, declines, or defers a possible trade. |
| Risk Engine | Enforces authoritative risk, margin, concentration, event, and portfolio constraints. |
| Execution / Order Manager | Builds, routes, modifies, and cancels approved orders. |

### 1.2 Frozen pipeline

```text
MarketSnapshot (+ optional HistoricalData / PortfolioSnapshot / RiskProfile)
  → IronCondorStrategy (BaseStrategy plugin)
  → TradingSignal + IronCondorRecommendation
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

- **BOUNDARY-ICS-001:** The strategy MUST NOT place an order.
- **BOUNDARY-ICS-002:** The strategy MUST NOT modify or cancel an order.
- **BOUNDARY-ICS-003:** The strategy MUST NOT create, reconcile, or manage a position.
- **BOUNDARY-ICS-004:** The strategy MUST NOT calculate authoritative portfolio risk.
- **BOUNDARY-ICS-005:** The strategy MUST NOT calculate authoritative margin.
- **BOUNDARY-ICS-006:** The strategy MUST NOT calculate position size.
- **BOUNDARY-ICS-007:** The strategy MUST NOT call a broker API.
- **BOUNDARY-ICS-008:** The strategy MUST NOT import or call `kiteconnect`.
- **BOUNDARY-ICS-009:** The strategy MUST NOT fetch a live quote or option chain.
- **BOUNDARY-ICS-010:** The strategy MUST NOT subscribe to a websocket.
- **BOUNDARY-ICS-011:** The strategy MUST NOT load `.env`, files, or credentials.
- **BOUNDARY-ICS-012:** The strategy MUST NOT replace the Evaluation Engine.
- **BOUNDARY-ICS-013:** The strategy MUST NOT replace the Trade Decision Engine.
- **BOUNDARY-ICS-014:** The strategy MUST NOT replace Risk or Execution.
- **BOUNDARY-ICS-015:** The strategy MUST NOT mutate `MarketSnapshot`.
- **BOUNDARY-ICS-016:** The strategy MUST NOT mutate `PortfolioSnapshot`.
- **BOUNDARY-ICS-017:** The strategy MUST NOT mutate context metadata.
- **BOUNDARY-ICS-018:** The strategy MUST NOT retain mutable caller-owned data.
- **BOUNDARY-ICS-019:** The strategy MUST NOT silently infer unavailable Greeks.
- **BOUNDARY-ICS-020:** The strategy MUST NOT represent a heuristic POP as a guarantee.
- **BOUNDARY-ICS-021:** The strategy MUST NOT label iron-condor max loss as undefined.
- **BOUNDARY-ICS-022:** The strategy MUST NOT suppress the defined-risk statement.
- **BOUNDARY-ICS-023:** The strategy MUST NOT use wall-clock time except injected context time.
- **BOUNDARY-ICS-024:** The strategy MUST NOT use randomness.
- **BOUNDARY-ICS-025:** The strategy MUST NOT publish a signal with invalid evidence.
- **BOUNDARY-ICS-026:** The strategy MUST NOT invent missing long wings to force an entry.
- **BOUNDARY-ICS-027:** The strategy MUST NOT emit a three-leg or two-leg structure as an iron condor.

### 1.4 Goals

1. Provide a single deterministic implementation of iron-condor suitability.
2. Prefer sideways / range-bound regimes with elevated implied volatility.
3. Reject trending, crisis, stale, incomplete, and illiquid conditions.
4. Reject high trend strength even when the regime tag is otherwise friendly.
5. Select four option legs: short put, long put, short call, and long call.
6. Select short strikes by configurable short-delta targets.
7. Select long strikes by configurable long-delta targets or wing-width rules.
8. Calculate expected net credit, max profit, max loss, and POP heuristic.
9. Explain every recommendation and every abstention.
10. Produce immutable artifacts that downstream components can serialize safely.
11. Integrate with the shared scoring framework without reimplementing scoring.
12. Make defined-risk geometry unambiguous to every consumer.
13. Permit deterministic unit tests with no broker or network dependency.
14. Preserve the locked platform pipeline.

### 1.5 Success criteria

- Equivalent valid inputs yield equivalent sealed outputs across runs and threads.
- Each `ENTER` recommendation contains exactly four legs with correct geometry.
- Each `ABSTAIN` or `REJECT` recommendation contains a stable machine code and reason.
- Candidate ranking is nearest delta, then tighter spread, then higher OI.
- Missing mandatory inputs fail closed before an entry recommendation.
- No production code path imports broker, websocket, credential, or environment facilities.
- Every entry artifact states that max loss is finite and `DEFINED_RISK`.
- Unit coverage of `strategy/iron_condor_strategy.py` is at least 95%.

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
| R7 | Inspect injected regime evidence for sideways / range suitability. |
| R8 | Inspect injected trend-strength evidence and reject high trend strength. |
| R9 | Inspect injected IV and IV-rank evidence. |
| R10 | Use injected history only when configuration permits fallback derivation. |
| R11 | Validate option-chain completeness for four legs. |
| R12 | Filter expired, malformed, ATM/ITM-ineligible, and non-OTM option contracts. |
| R13 | Filter contracts outside liquidity thresholds. |
| R14 | Select a compatible expiry deterministically. |
| R15 | Select the short call from OTM call candidates. |
| R16 | Select the short put from OTM put candidates. |
| R17 | Select the long call further OTM than the short call. |
| R18 | Select the long put further OTM than the short put. |
| R19 | Validate iron-condor strike geometry. |
| R20 | Calculate configured premium using MID or ASK/BID policy. |
| R21 | Calculate expected net credit. |
| R22 | Calculate maximum profit as net credit × multiplier. |
| R23 | Calculate maximum loss from wing widths minus net credit. |
| R24 | Calculate a documented POP heuristic. |
| R25 | Produce scoring-factor inputs with provenance. |
| R26 | Call `StrategyScoringFramework.score()` only after gates pass or with explicit abstention evidence. |
| R27 | Map sealed scoring artifacts to `TradingSignal`. |
| R28 | Include a four-leg structure hint for entries. |
| R29 | Include stable, ordered explanatory reasons. |
| R30 | Produce an immutable plugin-internal evaluation artifact. |
| R31 | Serialize public models using versioned canonical payloads. |
| R32 | Reject invalid deserialized payloads. |
| R33 | Support optional observational event publication through an injected sink. |
| R34 | Preserve an informational risk-profile hint without enforcing it. |
| R35 | Preserve an informational portfolio snapshot without mutating or pricing it. |
| R36 | Provide deterministic ranking keys for evaluation-engine consumption. |
| R37 | Make all gate outcomes auditable with identifiers and observed values. |
| R38 | Support safe empty-chain abstention. |
| R39 | Keep strategy state stateless and thread-safe. |
| R40 | Produce `StrategyScore`, `ConfidenceReport`, and `StrategyExplanation`. |

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

---

## 4. Strategy identity and registry metadata

The registration key is exactly `iron_condor`. It is lowercase, stable, and
not user-configurable.

| Metadata field | Required value |
|---|---|
| `strategy_id` | `iron_condor` |
| `display_name` | `Iron Condor` |
| `family` | `iron_condor` |
| `version` | `1.0.0` |
| `direction` | `NEUTRAL` / `SHORT_VOL` |
| `risk_profile_hint` | `DEFINED` / `DEFINED_RISK` |
| `required_structure` | Four option legs (short put, long put, short call, long call) |
| `scoring_profile_default` | `PREMIUM_SELLING` |
| `supports_direct_execution` | `false` |
| `supports_position_management` | `false` |

**REG-ICS-001:** `strategy/registry.py` MUST register the class under
`iron_condor`.

**REG-ICS-002:** Duplicate registration MUST fail at registry construction.

**REG-ICS-003:** The registration factory MUST receive immutable configuration
and optional injected collaborators only.

**REG-ICS-004:** Registry metadata MUST advertise defined / finite risk.

**REG-ICS-005:** A registry consumer MUST NOT infer that registration authorizes
trading.

**REG-ICS-006:** The family enum value MUST be `StrategyFamily.IRON_CONDOR`.

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
5. Trend-strength suitability.
6. IV and IV-rank suitability.
7. Option-chain completeness.
8. Liquidity.
9. Expiry and four-leg strike selection.
10. Geometry, premium, and metric validation.
11. Scoring and signal sealing.

### 5.2 Regime gate

| Regime tag | Default outcome | Rule |
|---|---|---|
| `RANGE_BOUND` | PASS | Preferred when other gates pass. |
| `MEAN_REVERTING` | PASS | Permitted with no crisis flag. |
| `SIDEWAYS` | PASS | Alias of range-bound suitability when explicitly supplied. |
| `NEUTRAL` | ABSTAIN | Insufficient positive evidence by default. |
| `TRENDING_UP` | ABSTAIN | Directional trend is unsuitable for iron-condor neutrality. |
| `TRENDING_DOWN` | ABSTAIN | Directional trend is unsuitable for iron-condor neutrality. |
| `BREAKOUT` | ABSTAIN | Expansion risk is unsuitable. |
| `HIGH_VOLATILITY_CRISIS` | REJECT | Crisis condition violates entry policy. |
| absent | REJECT | Required regime evidence is absent. |

- **GATE-ICS-001:** Only explicit regime tags may be used.
- **GATE-ICS-002:** A regime score cannot overturn an unsuitable regime tag.
- **GATE-ICS-003:** A crisis tag is an immediate reject.
- **GATE-ICS-004:** A contradictory set of supplied tags is a reject.
- **GATE-ICS-005:** Regime evidence must identify its observation timestamp.
- **GATE-ICS-006:** The strategy prefers sideways / range markets and MUST NOT
  invent a sideways regime from price action.

### 5.3 Trend-strength gate

Iron Condors are especially sensitive to directional expansion. Even when the
regime tag is range-friendly, elevated trend strength abstains.

| Trend-strength evidence | Default outcome |
|---|---|
| Missing when `require_trend_strength` is true | REJECT |
| Non-finite | REJECT |
| `>= maximum_trend_strength` | ABSTAIN |
| `< maximum_trend_strength` | PASS |

- **GATE-ICS-007:** High trend strength MUST abstain even under `RANGE_BOUND`.
- **GATE-ICS-008:** Trend strength is informational suitability only; it is not
  a risk limit.
- **GATE-ICS-009:** Trend strength MUST be injected; it is not fetched.

### 5.4 IV and IV-rank gate

`iv_rank` is a supplied bounded percentile in `[0, 100]`. It is not inferred
from a broker call. If injected `HistoricalSeries` is used to calculate an
allowed fallback rank, the complete series must already be in the context.

- **GATE-ICS-010:** `iv_rank >= minimum_iv_rank` is mandatory.
- **GATE-ICS-011:** Non-finite IV or IV rank is rejected.
- **GATE-ICS-012:** Missing IV rank is rejected when `require_iv_rank` is true.
- **GATE-ICS-013:** A fallback rank may be used only when configuration enables it.
- **GATE-ICS-014:** A fallback requires at least `iv_rank_lookback_observations`.
- **GATE-ICS-015:** IV rank is a suitability signal, never a profitability guarantee.
- **GATE-ICS-016:** Low IV relative to `minimum_iv_rank` abstains (`ICS.IV_RANK.LOW`).

### 5.5 Liquidity gate

Every selected leg must independently pass liquidity. The structure passes only
when all four legs pass.

| Metric | Default interpretation |
|---|---|
| Bid | Must be finite and non-negative. |
| Ask | Must be finite, positive, and at least bid. |
| Absolute spread | `ask - bid <= maximum_spread_width`. |
| Relative spread | `(ask - bid) / midpoint <= maximum_relative_spread_width`. |
| Open interest | `oi >= minimum_open_interest`. |
| Volume | `volume >= minimum_volume`. |
| Quote time | Within configured quote-age threshold if available. |

- **GATE-ICS-020:** Missing bid or ask rejects the affected contract.
- **GATE-ICS-021:** Crossed quotes reject the affected contract.
- **GATE-ICS-022:** Zero or negative midpoint rejects the affected contract.
- **GATE-ICS-023:** OI below the floor abstains for that candidate.
- **GATE-ICS-024:** Volume below the floor abstains for that candidate.
- **GATE-ICS-025:** Spread above either enabled limit abstains for that candidate.
- **GATE-ICS-026:** An absent optional OI field rejects when OI is required.
- **GATE-ICS-027:** An absent optional volume field rejects when volume is required.
- **GATE-ICS-028:** Poor liquidity on any of the four selected legs abstains the structure.

### 5.6 Time-window gate

The configuration contains explicit exchange-local entry and informational exit
windows. Context supplies the observed timestamp and exchange timezone.

- **GATE-ICS-030:** Entry is permitted only inside an inclusive start and exclusive end interval.
- **GATE-ICS-031:** The exit window is copied to metadata and is never acted on.
- **GATE-ICS-032:** Missing timezone data rejects a time-window evaluation.
- **GATE-ICS-033:** A timestamp on the end boundary abstains.
- **GATE-ICS-034:** Cross-midnight windows are rejected in v1.0.
- **GATE-ICS-035:** The plugin never waits for a future window.

### 5.7 Chain-completeness gate

- **GATE-ICS-040:** Underlying spot must be finite and strictly positive.
- **GATE-ICS-041:** At least one eligible OTM short-call candidate must exist.
- **GATE-ICS-042:** At least one eligible OTM short-put candidate must exist.
- **GATE-ICS-043:** At least one eligible long-call candidate further OTM than the short call must exist.
- **GATE-ICS-044:** At least one eligible long-put candidate further OTM than the short put must exist.
- **GATE-ICS-045:** All four legs must share a selected expiry.
- **GATE-ICS-046:** Required Greek fields must be present for every selected leg.
- **GATE-ICS-047:** Contract strike, expiry, type, and quote identity must agree.
- **GATE-ICS-048:** Duplicate instrument identifiers with conflicting facts reject the snapshot.
- **GATE-ICS-049:** Insufficient option chain for four distinct instruments abstains or rejects with `ICS.CHAIN.INCOMPLETE`.

### 5.8 Snapshot and context rejects

| Condition | Code | State |
|---|---|---|
| Missing market snapshot | `ICS.SNAPSHOT.MISSING` | REJECT |
| Stale snapshot | `ICS.SNAPSHOT.STALE` | REJECT |
| Unsupported underlying | `ICS.UNDERLYING.UNSUPPORTED` | REJECT |
| Outside entry window | `ICS.TIME.OUTSIDE_ENTRY_WINDOW` | ABSTAIN |
| Regime missing | `ICS.REGIME.MISSING` | REJECT |
| Regime crisis | `ICS.REGIME.CRISIS` | REJECT |
| Regime unsuitable | `ICS.REGIME.UNSUITABLE` | ABSTAIN |
| High trend strength | `ICS.TREND.HIGH_STRENGTH` | ABSTAIN |
| Adverse event | `ICS.EVENT.ADVERSE` | ABSTAIN |
| IV rank missing | `ICS.IV_RANK.MISSING` | REJECT |
| IV rank low | `ICS.IV_RANK.LOW` | ABSTAIN |
| Poor liquidity | `ICS.LIQUIDITY.POOR` | ABSTAIN |
| Incomplete chain | `ICS.CHAIN.INCOMPLETE` | REJECT |
| Missing Greeks | `ICS.GREEKS.MISSING` | REJECT |
| Invalid geometry | `ICS.STRUCTURE.INVALID_GEOMETRY` | REJECT |
| Credit below floor | `ICS.PREMIUM.BELOW_MINIMUM` | ABSTAIN |
| Non-positive max loss | `ICS.RISK.NON_POSITIVE_MAX_LOSS` | REJECT |

---

## 6. Strike selection algorithm

The algorithm selects four legs for a single expiry:

1. Short put (SELL PE)
2. Long put (BUY PE), further OTM than the short put
3. Short call (SELL CE)
4. Long call (BUY CE), further OTM than the short call

It is deterministic and never calls a broker or market-data service.

### 6.1 Definitions

| Term | Definition |
|---|---|
| OTM call | Contract with `strike > spot`. |
| OTM put | Contract with `strike < spot`. |
| Short-call target | `abs(config.short_call_target_delta)` or shared `short_target_delta`. |
| Short-put target | `abs(config.short_put_target_delta)` or shared `short_target_delta`. |
| Long-call target | `abs(config.long_call_target_delta)` or shared `long_target_delta`. |
| Long-put target | `abs(config.long_put_target_delta)` or shared `long_target_delta`. |
| Delta error | `abs(abs(contract.delta) - target_delta)`. |
| Put wing width | `short_put_strike - long_put_strike` (strictly positive). |
| Call wing width | `long_call_strike - short_call_strike` (strictly positive). |
| Eligible short contract | Correct type, selected expiry, OTM, valid quote, liquidity-pass, valid required Greek, within short-delta tolerance. |
| Eligible long contract | Correct type, selected expiry, further OTM than the paired short, valid quote, liquidity-pass, valid required Greek, within long-delta or wing-width policy. |

### 6.2 Expiry selection

1. Group valid contracts by expiry.
2. Exclude expiries earlier than the context observation date.
3. Exclude expiries outside configured DTE bounds.
4. Retain expiries containing candidates for all four roles.
5. Choose the expiry with the lowest non-negative DTE.
6. If DTE ties, choose the earlier normalized expiry timestamp.
7. If normalized expiry ties, choose lexicographically smallest expiry identifier.

- **STRIKE-ICS-001:** Expiry selection is completed before leg selection.
- **STRIKE-ICS-002:** A same-day expiry is permitted only if `minimum_dte == 0`.
- **STRIKE-ICS-003:** Expired contracts are never candidates.
- **STRIKE-ICS-004:** All four legs MUST use the same expiry.

### 6.3 Short-leg candidate ranking

For each short side, sort eligible candidates by this ascending tuple:

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

- **STRIKE-ICS-010:** Short delta error must not exceed `short_delta_selection_tolerance`.
- **STRIKE-ICS-011:** The selected short CE must be OTM at evaluation spot.
- **STRIKE-ICS-012:** The selected short PE must be OTM at evaluation spot.
- **STRIKE-ICS-013:** Equal ranking tuples are resolved by `instrument_id`.
- **STRIKE-ICS-014:** Floating values are compared as normalized decimals.
- **STRIKE-ICS-015:** The input option-chain order must not influence selection.

### 6.4 Long-leg / wing selection

After short legs are fixed, long legs are selected under one configured policy:

| Policy | Behavior |
|---|---|
| `DELTA_TARGET` | Rank further-OTM candidates by long-delta proximity. |
| `FIXED_WIDTH` | Prefer contracts whose strike distance equals `target_wing_width`. |
| `WIDTH_THEN_DELTA` | Filter by wing-width bounds, then rank by long-delta proximity. |

Long-call candidates must satisfy `strike > short_call_strike`.
Long-put candidates must satisfy `strike < short_put_strike`.

Long-leg ranking tuple (DELTA_TARGET / WIDTH_THEN_DELTA):

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

- **STRIKE-ICS-020:** Long CE strike MUST exceed short CE strike.
- **STRIKE-ICS-021:** Long PE strike MUST be below short PE strike.
- **STRIKE-ICS-022:** Wing widths MUST be strictly positive.
- **STRIKE-ICS-023:** Wing widths MUST satisfy configured min/max bounds when set.
- **STRIKE-ICS-024:** Asymmetric wings are permitted when configuration allows.
- **STRIKE-ICS-025:** When `require_symmetric_wings` is true, put and call wing
  widths MUST be equal after decimal normalization.
- **STRIKE-ICS-026:** Long delta magnitude MUST be strictly less than the paired
  short delta magnitude when both Greeks are present.

### 6.5 Pseudocode

```python
def choose_short_leg(
    contracts: tuple[OptionContract, ...],
    side: OptionType,
    spot: Decimal,
    expiry: date,
    target_delta: Decimal,
    config: IronCondorConfiguration,
) -> OptionContract | GateFailure:
    candidates = [
        contract
        for contract in contracts
        if is_eligible_short(contract, side, spot, expiry, config)
        and abs(abs(contract.delta) - target_delta)
        <= config.short_delta_selection_tolerance
    ]
    if not candidates:
        return GateFailure("ICS.STRIKE.NO_ELIGIBLE_SHORT")
    return min(candidates, key=short_candidate_rank_key)


def choose_long_leg(
    contracts: tuple[OptionContract, ...],
    side: OptionType,
    spot: Decimal,
    expiry: date,
    short_leg: OptionContract,
    target_delta: Decimal,
    config: IronCondorConfiguration,
) -> OptionContract | GateFailure:
    candidates = [
        contract
        for contract in contracts
        if is_eligible_long(contract, side, spot, expiry, short_leg, config)
    ]
    if not candidates:
        return GateFailure("ICS.STRIKE.NO_ELIGIBLE_LONG")
    return min(candidates, key=long_candidate_rank_key)


def select_iron_condor(...):
    expiry = choose_expiry(...)
    short_put = choose_short_leg(..., PE, short_put_target)
    short_call = choose_short_leg(..., CE, short_call_target)
    long_put = choose_long_leg(..., PE, short_put, long_put_target)
    long_call = choose_long_leg(..., CE, short_call, long_call_target)
    validate_geometry(long_put, short_put, spot, short_call, long_call)
    return IronCondorStrikeSelection(...)
```

### 6.6 Structure geometry validation

Required ordering after selection:

```text
long_put_strike < short_put_strike < spot < short_call_strike < long_call_strike
```

- **STRIKE-ICS-030:** Selected contracts MUST have four distinct instrument IDs.
- **STRIKE-ICS-031:** Selected contracts MUST have the same underlying.
- **STRIKE-ICS-032:** Selected contracts MUST have the selected expiry.
- **STRIKE-ICS-033:** Selected contract deltas MUST retain their original signs.
- **STRIKE-ICS-034:** Recommendation leg sides are `SELL, BUY, SELL, BUY` as a
  structure hint only, never as an order request.
- **STRIKE-ICS-035:** Canonical leg index order for structure hints MUST be:

| Index | Role | Side | Option type |
|---|---|---|---|
| 0 | Short put | SELL | PE |
| 1 | Long put | BUY | PE |
| 2 | Short call | SELL | CE |
| 3 | Long call | BUY | CE |

This ordering is compatible with `execution/execution_engine.py` family-side
resolution for `StrategyFamily.IRON_CONDOR`.

---

## 7. Premium, POP, and risk metrics

All calculations use `Decimal` internally and are rounded only when sealing
public outputs. Monetary values are expressed in snapshot currency units per
underlying unit unless a multiplier is explicitly supplied.

### 7.1 Price policy

| Policy | Short-leg credit price | Long-leg debit price | Use |
|---|---|---|---|
| `MID` | `(bid + ask) / 2` | `(bid + ask) / 2` | Neutral estimate. |
| `CONSERVATIVE` | `bid` for shorts | `ask` for longs | Conservative net-credit estimate. |
| `ASK_CREDIT` | `ask` for shorts | `bid` for longs | Aggressive credit / optimistic estimate; allowed only when explicitly configured. |

**METRIC-ICS-001:** A policy is applied consistently across all four legs.

**METRIC-ICS-002:** If a required quote is unavailable, the strategy abstains.

**METRIC-ICS-003:** The policy estimate is not an executable fill prediction.

**METRIC-ICS-004:** Default v1.0 policy is `MID`.

### 7.2 Credit, maximum profit, and maximum loss

```text
short_put_credit  = price_short(selected_short_put, policy)
long_put_debit    = price_long(selected_long_put, policy)
short_call_credit = price_short(selected_short_call, policy)
long_call_debit   = price_long(selected_long_call, policy)

put_credit  = short_put_credit - long_put_debit
call_credit = short_call_credit - long_call_debit
net_credit  = put_credit + call_credit

put_wing_width  = short_put_strike - long_put_strike
call_wing_width = long_call_strike - short_call_strike
max_wing_width  = max(put_wing_width, call_wing_width)

max_profit = net_credit × contract_multiplier
max_loss   = (max_wing_width - net_credit) × contract_multiplier
```

For a standard credit iron condor, maximum profit is the received net credit
when the underlying expires between the short strikes. Maximum loss is the
wider wing width minus net credit (times multiplier). Fees, taxes, slippage,
assignment, and execution costs are excluded unless already represented by
injected facts.

- **METRIC-ICS-010:** `net_credit` MUST be strictly positive to ENTER.
- **METRIC-ICS-011:** `net_credit >= minimum_premium` is mandatory for ENTER.
- **METRIC-ICS-012:** `max_loss` MUST be finite and strictly positive.
- **METRIC-ICS-013:** If `max_wing_width <= net_credit`, reject with
  `ICS.RISK.NON_POSITIVE_MAX_LOSS` (degenerate geometry / pricing).
- **METRIC-ICS-014:** The strategy MUST label max loss as `DEFINED_RISK`.
- **METRIC-ICS-015:** The strategy MUST NOT substitute margin for max loss.
- **METRIC-ICS-016:** The strategy MUST NOT invent an infinite loss.

### 7.3 Probability-of-profit heuristic

The v1.0 POP is a transparent ranking heuristic:

```text
call_short_otm_probability = clamp(1 - abs(short_call_delta), 0, 1)
put_short_otm_probability  = clamp(1 - abs(short_put_delta), 0, 1)
joint_short_otm_heuristic  = max(0, call_short_otm_probability + put_short_otm_probability - 1)
credit_adjustment          = min(net_credit / max(spot, epsilon), 0.05)
defined_risk_adjustment    = min(net_credit / max(max_loss / multiplier, epsilon), 0.05)
pop = clamp(
    joint_short_otm_heuristic + 0.5 * credit_adjustment + 0.5 * defined_risk_adjustment,
    0,
    1,
)
```

The formula intentionally avoids claiming independence between the wings.
It is not a pricing model, distribution model, backtest, guarantee, or risk
limit. `epsilon` is an internal positive decimal used only after spot and
max-loss positivity validation.

### 7.4 Risk statement

| Metric | Required v1.0 value |
|---|---|
| `max_profit` | Estimated net credit multiplied by multiplier. |
| `max_loss` | Finite `(max_wing_width - net_credit) × multiplier`. |
| `max_loss_label` | `DEFINED_RISK`. |
| `risk_profile_hint` | `DEFINED`. |
| `capital_at_risk` | Informational copy of `max_loss`; Risk Engine owns authoritative calculation. |
| `margin_required` | `None`; Risk Engine / broker owns authoritative calculation. |
| `breakevens` | Informational: `short_put - net_credit` and `short_call + net_credit` when calculable. |
| `reward_risk_ratio` | Informational: `max_profit / max_loss` when `max_loss > 0`. |

**METRIC-ICS-020:** Every entry explanation MUST include the defined-risk statement.

**METRIC-ICS-021:** Every entry artifact MUST expose both wing widths.

**METRIC-ICS-022:** Breakevens are informational only and never become stops.

---

## 8. Scoring integration

The strategy extracts facts and calls `StrategyScoringFramework.score()` with a
`FactorInputBundle`. The framework owns normalization, weighting, confidence
math, explanation sealing, and score serialization.

### 8.1 PREMIUM_SELLING factor map

| Factor category | Source | Strategy mapping |
|---|---|---|
| `MARKET_REGIME` | Injected regime tag and score | Range-bound / sideways / mean-reverting suitability. |
| `TREND_ALIGNMENT` | Injected trend evidence and trend strength | Penalizes directional trend and high trend strength. |
| `VOLATILITY` | IV rank and IV evidence | Rewards elevated IV above floor. |
| `LIQUIDITY` | Selected-leg quote/OI/volume facts | Rewards tight, liquid selected legs across all four. |
| `GREEKS` | Selected short and long deltas | Rewards target proximity and balanced wing magnitudes. |
| `RISK_REWARD` | Credit, POP heuristic, defined max loss | Score is suitability only; never conceals loss geometry. |
| `EVENT_RISK` | Injected event flags | Penalizes known elevated event risk. |

- **SCORE-ICS-001:** Factor provenance MUST identify snapshot or injected metadata origin.
- **SCORE-ICS-002:** No factor may be fabricated to fill missing mandatory evidence.
- **SCORE-ICS-003:** The score profile defaults to `PREMIUM_SELLING`.
- **SCORE-ICS-004:** Unknown profile names reject configuration.
- **SCORE-ICS-005:** A sealed score does not authorize an entry.
- **SCORE-ICS-006:** Defined-risk reward/risk facts MUST appear in explanation text.

### 8.2 Confidence mapping

The strategy forwards the framework-produced `ConfidenceReport` unchanged.
`SignalConfidence` is mapped from its band using the common project mapping.
An abstention may have high confidence: high confidence can mean strong
evidence that conditions are unsuitable.

### 8.3 Explanation requirements

Every sealed `StrategyExplanation` for an ENTER recommendation MUST include:

1. Selected expiry and four strikes.
2. Net credit, max profit, and max loss.
3. Defined-risk label.
4. Regime and IV-rank evidence identifiers.
5. Stable reason codes in gate order.

---

## 9. TradingSignal mapping

| Recommendation state | TradingSignal action | Structure hint | Meaning |
|---|---|---|---|
| `ENTER` | `ENTER` or project-equivalent evaluate/entry action | Four legs | Suitable analytical candidate; downstream approval required. |
| `ABSTAIN` | `ABSTAIN` | None | Valid context, insufficient suitability now. |
| `REJECT` | `REJECT` | None | Invalid, stale, unsupported, or prohibited input. |

For `ENTER`, direction is `NEUTRAL` or `SHORT_VOL`; the structure hint contains
four legs in canonical index order with exact selected contract identity. It is
a declarative recommendation, never an order request.

- **SIGNAL-ICS-001:** Signal reasons are stable and ordered by gate sequence.
- **SIGNAL-ICS-002:** `ENTER` includes score, confidence, explanation, and recommendation ID.
- **SIGNAL-ICS-003:** `ABSTAIN` includes all successful gate observations before the first failure.
- **SIGNAL-ICS-004:** `REJECT` includes a stable error code and safe details.
- **SIGNAL-ICS-005:** A signal does not expose credentials, portfolio account identifiers, or raw secrets.
- **SIGNAL-ICS-006:** `ENTER` risk metadata uses `RiskProfileHint.DEFINED` and
  `max_loss_category="DEFINED_RISK"`.
- **SIGNAL-ICS-007:** Structure type string is `iron_condor`.

---

## 10. Configuration

`IronCondorConfiguration` is a frozen dataclass. All values are validated at
construction; an invalid configuration cannot be used to evaluate a snapshot.

| Field | Type | Default | Validation |
|---|---|---|---|
| `short_target_delta` | `Decimal` | `0.16` | `(0, 0.50)` |
| `long_target_delta` | `Decimal` | `0.05` | `(0, short_target_delta)` |
| `short_call_target_delta` | `Decimal \| None` | `None` | Uses shared short target when absent. |
| `short_put_target_delta` | `Decimal \| None` | `None` | Uses shared short target when absent. |
| `long_call_target_delta` | `Decimal \| None` | `None` | Uses shared long target when absent. |
| `long_put_target_delta` | `Decimal \| None` | `None` | Uses shared long target when absent. |
| `wing_selection_policy` | `WingSelectionPolicy` | `WIDTH_THEN_DELTA` | Known enum. |
| `target_wing_width` | `Decimal \| None` | `None` | Positive when set. |
| `minimum_wing_width` | `Decimal \| None` | `None` | Positive when set. |
| `maximum_wing_width` | `Decimal \| None` | `None` | At least minimum when both set. |
| `require_symmetric_wings` | `bool` | `False` | Boolean. |
| `minimum_iv_rank` | `Decimal` | `50` | `[0, 100]` |
| `maximum_trend_strength` | `Decimal` | `0.55` | `[0, 1]` |
| `require_trend_strength` | `bool` | `True` | Boolean. |
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
| `allow_asymmetric_wings` | `bool` | `True` | Boolean; overridden by `require_symmetric_wings`. |
| `contract_multiplier` | `Decimal` | `1` | Positive. |

### 10.1 Configuration invariants

- **CFG-ICS-001:** Decimal fields must be finite.
- **CFG-ICS-002:** A short-call target overrides only the short-call target.
- **CFG-ICS-003:** A short-put target overrides only the short-put target.
- **CFG-ICS-004:** A long-call target overrides only the long-call target.
- **CFG-ICS-005:** A long-put target overrides only the long-put target.
- **CFG-ICS-006:** Short target magnitudes must be strictly less than 0.50.
- **CFG-ICS-007:** Long target magnitudes must be strictly less than the paired short targets.
- **CFG-ICS-008:** `minimum_premium` is evaluated on total net credit.
- **CFG-ICS-009:** An empty underlying set is invalid.
- **CFG-ICS-010:** Underlying strings are normalized to uppercase at construction.
- **CFG-ICS-011:** `maximum_dte < minimum_dte` is invalid.
- **CFG-ICS-012:** A `None` absolute spread limit disables only that limit.
- **CFG-ICS-013:** Exit-window configuration never becomes exit behavior.
- **CFG-ICS-014:** `require_symmetric_wings=True` forces `allow_asymmetric_wings=False`.
- **CFG-ICS-015:** `long_target_delta >= short_target_delta` is invalid.
- **CFG-ICS-016:** `maximum_trend_strength` must be finite and in `[0, 1]`.
- **CFG-ICS-017:** Only `PREMIUM_SELLING` is supported in v1.0.

---

## 11. Frozen public models

### 11.1 `IronCondorStrikeSelection`

| Field | Type | Meaning |
|---|---|---|
| `underlying` | `str` | Normalized underlying identity. |
| `spot` | `Decimal` | Evaluation spot. |
| `expiry` | `date` | Shared selected expiry. |
| `long_put_strike` | `Decimal` | Selected long PE strike. |
| `short_put_strike` | `Decimal` | Selected short PE strike. |
| `short_call_strike` | `Decimal` | Selected short CE strike. |
| `long_call_strike` | `Decimal` | Selected long CE strike. |
| `long_put_instrument_id` | `str` | Immutable instrument identity. |
| `short_put_instrument_id` | `str` | Immutable instrument identity. |
| `short_call_instrument_id` | `str` | Immutable instrument identity. |
| `long_call_instrument_id` | `str` | Immutable instrument identity. |
| `long_put_delta` | `Decimal` | Observed delta. |
| `short_put_delta` | `Decimal` | Observed delta. |
| `short_call_delta` | `Decimal` | Observed delta. |
| `long_call_delta` | `Decimal` | Observed delta. |
| `put_wing_width` | `Decimal` | `short_put - long_put`. |
| `call_wing_width` | `Decimal` | `long_call - short_call`. |
| `dte` | `int` | Days to expiry at evaluation date. |

### 11.2 `IronCondorRiskMetrics`

| Field | Type | Meaning |
|---|---|---|
| `net_credit` | `Decimal` | Estimated structure credit. |
| `max_profit` | `Decimal` | `net_credit × multiplier`. |
| `max_loss` | `Decimal` | Finite defined loss. |
| `max_loss_label` | `str` | Always `DEFINED_RISK`. |
| `probability_of_profit` | `Decimal` | Heuristic in `[0, 1]`. |
| `reward_risk_ratio` | `Decimal` | `max_profit / max_loss`. |
| `lower_breakeven` | `Decimal \| None` | Informational. |
| `upper_breakeven` | `Decimal \| None` | Informational. |
| `put_credit` | `Decimal` | Put-wing net credit. |
| `call_credit` | `Decimal` | Call-wing net credit. |
| `contract_multiplier` | `Decimal` | Applied multiplier. |

### 11.3 `IronCondorRecommendation`

| Field | Type | Meaning |
|---|---|---|
| `recommendation_id` | `str` | Stable deterministic identifier. |
| `state` | `EntryRecommendationState` | `ENTER`, `ABSTAIN`, or `REJECT`. |
| `strategy_id` | `str` | Always `iron_condor`. |
| `as_of` | `datetime` | Evaluation timestamp from context. |
| `strike_selection` | `IronCondorStrikeSelection \| None` | Present on ENTER. |
| `risk_metrics` | `IronCondorRiskMetrics \| None` | Present on ENTER. |
| `strategy_score` | `StrategyScore \| None` | Sealed score when available. |
| `confidence` | `ConfidenceReport \| None` | Sealed confidence when available. |
| `explanation` | `StrategyExplanation \| None` | Sealed explanation when available. |
| `reasons` | `tuple[str, ...]` | Ordered machine-readable reason codes. |
| `schema_version` | `str` | Serialization schema version. |

### 11.4 `IronCondorEvaluationResult`

| Field | Type | Meaning |
|---|---|---|
| `recommendation` | `IronCondorRecommendation` | Immutable recommendation artifact. |
| `signal` | `TradingSignal` | Framework-compatible signal. |

### 11.5 Supporting models

| Model | Role |
|---|---|
| `MarketRegimeEvidence` | Injected regime tag/score/as-of. |
| `TrendStrengthEvidence` | Injected trend-strength score/as-of. |
| `EventRiskEvidence` | Injected adverse-event flag. |
| `TimeWindow` | Exchange-local start/end interval. |
| `IronCondorContext` | Optional typed wrapper over `StrategyContext` facts. |
| `PremiumPricePolicy` | Quote policy enum. |
| `WingSelectionPolicy` | Long-leg selection policy enum. |
| `EntryRecommendationState` | `ENTER` / `ABSTAIN` / `REJECT`. |

All public models are immutable (`frozen=True`) dataclasses.

---

## 12. Public API

```python
class IronCondorStrategy(BaseStrategy):
    def __init__(
        self,
        configuration: IronCondorConfiguration,
        scoring_framework: StrategyScoringFramework,
        *,
        plugin_config: StrategyPluginConfig | None = None,
        event_sink: object | None = None,
    ) -> None: ...

    def evaluate(self, context: object) -> object: ...
    def evaluate_recommendation(
        self, context: StrategyContext | IronCondorContext
    ) -> IronCondorRecommendation: ...
    def evaluate_iron_condor(
        self, context: StrategyContext | IronCondorContext
    ) -> IronCondorRecommendation: ...
    def _execute(self, context: StrategyContext) -> TradingSignal: ...


def default_iron_condor_configuration() -> IronCondorConfiguration: ...
def to_json(recommendation: IronCondorRecommendation) -> str: ...
def from_json(payload: str) -> IronCondorRecommendation: ...
```

### 12.1 StrategyContext extensions

Evidence may be supplied through:

1. Frozen `IronCondorContext` wrapping `StrategyContext` plus typed evidence.
2. `StrategyContext.tags` string map with documented keys such as:
   - `regime_tag`
   - `iv_rank`
   - `trend_strength`
   - `event_adverse`

Tags remain `Mapping[str, str]`. Numeric evidence parsed from tags MUST use
deterministic decimal parsing and fail closed on malformed values.

### 12.2 Dispatch rules

- `StrategyContext` / `IronCondorContext` → recommendation path.
- Generic `EngineContext` → `BaseStrategy.evaluate` behavior unchanged.
- Inherited `run(StrategyContext)` → `_execute` → `TradingSignal`.

---

## 13. Validation

Validation is fail closed and ordered.

| Stage | Examples |
|---|---|
| Configuration | Targets, wing bounds, DTE, IV floors, trend ceiling. |
| Context | Snapshot presence, as-of timezone awareness. |
| Freshness | Snapshot age versus `max_snapshot_age_seconds`. |
| Underlying | Membership in `supported_underlyings`. |
| Time | Entry window inclusion. |
| Regime | Sideways / range suitability; crisis reject. |
| Trend | High trend-strength abstention. |
| IV | Minimum IV rank. |
| Chain | Four-role candidate existence. |
| Liquidity | Per-leg quote, OI, volume, spread. |
| Strikes | Geometry and distinct instruments. |
| Metrics | Positive credit, positive defined max loss. |
| Scoring | Framework acceptance of factor bundle. |

Reject missing market snapshot, stale data, poor liquidity, low IV, high trend
strength, and insufficient option chain exactly as required by this contract.

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
| Incompatible version | Reject with `ICS.SERIALIZATION.UNSUPPORTED_VERSION`. |
| Round trip | `from_json(to_json(x))` preserves semantic equality. |

Serialization MUST NEVER embed credentials, account numbers, or broker tokens.

---

## 16. Error catalog

| Code | Meaning |
|---|---|
| `ICS.CONFIG.INVALID` | Configuration invariant failed. |
| `ICS.CONTEXT.INVALID` | Context malformed. |
| `ICS.SNAPSHOT.MISSING` | Market snapshot absent. |
| `ICS.SNAPSHOT.STALE` | Snapshot older than allowed age. |
| `ICS.UNDERLYING.UNSUPPORTED` | Underlying not allowed. |
| `ICS.TIME.OUTSIDE_ENTRY_WINDOW` | Outside configured entry window. |
| `ICS.REGIME.MISSING` | Regime evidence absent. |
| `ICS.REGIME.CRISIS` | Crisis regime. |
| `ICS.REGIME.UNSUITABLE` | Non-sideways / trending regime. |
| `ICS.TREND.MISSING` | Trend strength required but absent. |
| `ICS.TREND.HIGH_STRENGTH` | Trend strength at or above ceiling. |
| `ICS.EVENT.ADVERSE` | Adverse event evidence. |
| `ICS.IV_RANK.MISSING` | IV rank required but absent. |
| `ICS.IV_RANK.LOW` | IV rank below floor. |
| `ICS.METRIC.NON_FINITE` | Non-finite suitability metric. |
| `ICS.CHAIN.INCOMPLETE` | Insufficient four-leg candidates. |
| `ICS.GREEKS.MISSING` | Required Greeks absent. |
| `ICS.LIQUIDITY.POOR` | Selected or candidate liquidity failed. |
| `ICS.STRIKE.NO_ELIGIBLE_SHORT` | No short-leg candidate. |
| `ICS.STRIKE.NO_ELIGIBLE_LONG` | No long-leg / wing candidate. |
| `ICS.STRUCTURE.INVALID_GEOMETRY` | Strike ordering invalid. |
| `ICS.PREMIUM.BELOW_MINIMUM` | Net credit below configured floor. |
| `ICS.RISK.NON_POSITIVE_MAX_LOSS` | Defined max loss not strictly positive. |
| `ICS.SCORING.FAILED` | Scoring framework rejected inputs. |
| `ICS.SERIALIZATION.UNSUPPORTED_VERSION` | Payload schema unsupported. |
| `ICS.SERIALIZATION.INVALID` | Payload malformed. |

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

1. Construct immutable `IronCondorConfiguration`.
2. Inject `StrategyScoringFramework`.
3. Register under `iron_condor`.
4. Evaluation Engine supplies `StrategyContext`.
5. Strategy returns `TradingSignal` via `run` and/or recommendation via evaluate APIs.
6. Trade Decision Engine consumes comparative reports.
7. Risk and Execution act only after independent approval.

### 18.1 Optional event topics

| Topic | When |
|---|---|
| `strategy.iron_condor.evaluated` | After sealed recommendation. |
| `strategy.iron_condor.abstained` | On ABSTAIN. |
| `strategy.iron_condor.rejected` | On REJECT. |
| `strategy.iron_condor.entered_candidate` | On ENTER candidate sealed. |

Event publication is observational only and MUST NOT gate the sealed result.

---

## 19. Testing

Unit tests live in `tests/test_iron_condor_strategy.py`.

Required coverage themes:

1. Configuration invariant failures.
2. Missing / stale snapshot rejects.
3. Regime and trend-strength gates.
4. Low IV abstention.
5. Liquidity failure on each of the four legs.
6. Incomplete chain rejects.
7. Deterministic four-leg selection and geometry.
8. Symmetric and asymmetric wing policies.
9. Net credit, max profit, max loss, POP calculations.
10. Minimum premium abstention.
11. Scoring integration happy path.
12. TradingSignal mapping and defined-risk metadata.
13. Serialization round trip and version rejection.
14. Concurrent identical evaluations.
15. Boundary greps proving no broker / order / risk imports.
16. Reversed chain order yields identical selection.

Target: greater than 95% line coverage of
`strategy/iron_condor_strategy.py`.

Tests MUST be deterministic and MUST NOT require network access.

---

## 20. Implementation checklist

- [ ] Create `strategy/iron_condor_strategy.py`.
- [ ] Create `tests/test_iron_condor_strategy.py`.
- [ ] Subclass `BaseStrategy`.
- [ ] Implement immutable configuration and public models.
- [ ] Implement gate ordering exactly as specified.
- [ ] Implement four-leg strike selection.
- [ ] Implement defined-risk metrics.
- [ ] Integrate `StrategyScoringFramework` with `PREMIUM_SELLING`.
- [ ] Map ENTER/ABSTAIN/REJECT to `TradingSignal`.
- [ ] Provide versioned `to_json` / `from_json`.
- [ ] Enforce all BOUNDARY-ICS rules.
- [ ] Achieve >95% unit coverage.
- [ ] Register identity `iron_condor` without placing orders.

---

## 21. Definition of Done

This strategy is done only when it evaluates whether an Iron Condor is
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
- Four-leg geometry is validated on every ENTER.
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

---

## Appendix A — Worked NIFTY evaluation

Example (illustrative, not a live quote):

| Input | Value |
|---|---|
| Underlying | NIFTY |
| Spot | 24500 |
| Regime | RANGE_BOUND |
| IV rank | 62 |
| Trend strength | 0.28 |
| Short target delta | 0.16 |
| Long target delta | 0.05 |

Selected structure (illustrative):

| Leg | Side | Type | Strike | Delta |
|---|---|---|---|---|
| 0 | SELL | PE | 24200 | -0.16 |
| 1 | BUY | PE | 24000 | -0.05 |
| 2 | SELL | CE | 24800 | 0.16 |
| 3 | BUY | CE | 25000 | 0.05 |

Derived metrics (illustrative):

| Metric | Value |
|---|---|
| Put wing width | 200 |
| Call wing width | 200 |
| Net credit | 45 |
| Max profit | 45 × multiplier |
| Max loss | (200 - 45) × multiplier = 155 × multiplier |
| Max loss label | DEFINED_RISK |

---

## Appendix B — Candidate selection examples

| Scenario | Expected behavior |
|---|---|
| Two short calls equal delta error | Prefer tighter relative spread, then higher OI, then strike, then instrument id. |
| Long call only ATM available | No eligible long; abstain/reject with no-eligible-long code. |
| Chain reversed | Identical sealed selection. |
| Duplicate conflicting instrument | Chain reject. |

---

## Appendix C — IV rank examples

| IV rank | Floor | Outcome |
|---|---|---|
| 62 | 50 | Pass IV gate. |
| 50 | 50 | Pass IV gate. |
| 49.999 | 50 | Abstain `ICS.IV_RANK.LOW`. |
| missing | required | Reject `ICS.IV_RANK.MISSING`. |
| NaN | any | Reject `ICS.METRIC.NON_FINITE`. |

---

## Appendix D — Liquidity rejects

| Leg failing liquidity | Outcome |
|---|---|
| Short put | Structure abstains; no ENTER. |
| Long put | Structure abstains; no ENTER. |
| Short call | Structure abstains; no ENTER. |
| Long call | Structure abstains; no ENTER. |

All four legs MUST independently pass before ENTER.

---

## Appendix E — Factor bundle example

A passing ENTER factor bundle includes regime suitability, low trend strength,
elevated IV rank, four-leg liquidity scores, short/long delta proximity,
defined reward/risk ratio, and non-adverse event evidence. Provenance points to
injected snapshot/tag/evidence identifiers only.

---

## Appendix F — TradingSignal example

An ENTER signal includes:

- `strategy_family = IRON_CONDOR`
- direction `NEUTRAL` or `SHORT_VOL`
- structure hint with four legs in canonical order
- risk profile `DEFINED`
- `max_loss_category = DEFINED_RISK`
- sealed score / confidence / explanation references
- ordered reason codes beginning with passed gates and ending with
  `ICS.RISK.DEFINED`

---

## Appendix G — Failure matrix

| Failure | State | Continues to scoring? |
|---|---|---|
| Missing snapshot | REJECT | No |
| Stale snapshot | REJECT | No |
| Crisis regime | REJECT | No |
| Trending regime | ABSTAIN | Optional abstention scoring only |
| High trend strength | ABSTAIN | Optional abstention scoring only |
| Low IV | ABSTAIN | Optional abstention scoring only |
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
| Iron Condor | Four-leg defined-risk short OTM put vertical + short OTM call vertical. |
| Short leg | Sold option nearer ATM. |
| Long leg / wing | Bought option further OTM that caps loss. |
| Wing width | Absolute strike distance between short and long on one side. |
| Defined risk | Maximum loss finite and known from geometry and credit. |
| POP heuristic | Transparent ranking probability, not a guarantee. |
| ENTER | Analytical candidate suitable for downstream decisioning. |

---

## Appendix J — Legacy migration

No legacy iron-condor plugin is assumed. If a prototype exists outside this
module path, it MUST NOT be imported. Consumers migrate by registering
`IronCondorStrategy` under `iron_condor` and consuming sealed recommendations.

---

## Appendix K — Benchmark contract

| Metric | Expectation |
|---|---|
| Evaluation complexity | Linear in option-chain size for filtering; selection uses deterministic min over filtered candidates. |
| Allocation | No unbounded per-call global caches. |
| External I/O | Zero. |

---

## Appendix L — Default profile rationale

Defaults favor moderately elevated IV, modest short deltas near 16-delta, far
wings near 5-delta, and explicit trend-strength rejection. These defaults are
suitability priors for Indian index options and remain configurable.

---

## Appendix M — Delta interpretation

Call deltas are expected non-negative; put deltas are expected non-positive.
Comparisons use absolute magnitudes. Sign violations on selected legs reject
geometry or Greeks validation.

---

## Appendix N — POP notes

POP is intentionally conservative and transparent. It uses short-leg OTM
heuristics plus small credit and defined-risk adjustments. It MUST NEVER be
labeled as Black-Scholes probability or backtested expectancy.

---

## Appendix O — Structured reason catalog

| Reason | Meaning |
|---|---|
| `ICS.GATES.PASS` | All mandatory gates passed. |
| `ICS.RISK.DEFINED` | Defined-risk statement attached. |
| `ICS.STRUCTURE.FOUR_LEGS` | Four-leg structure sealed. |
| `ICS.REGIME.RANGE_BOUND` | Range/sideways evidence observed. |
| `ICS.IV_RANK.PASS` | IV rank above floor. |
| `ICS.TREND.PASS` | Trend strength below ceiling. |
| `ICS.LIQUIDITY.PASS` | Four-leg liquidity passed. |

---

## Appendix P — Audit fields

Every recommendation SHOULD make the following auditable:

- recommendation id
- as-of timestamp
- underlying
- regime tag
- iv rank
- trend strength
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
| Execution Engine | May later map four-leg structure hints; this module never places orders. |
| Scoring Framework | `PREMIUM_SELLING` profile inputs only. |

---

## Appendix V — Rejection precedence

When multiple failures exist, emit the first failure in gate order. Do not
mask an earlier REJECT with a later ABSTAIN. Do not continue strike selection
after a hard REJECT.

---

## Appendix W — Entry payload constraints

ENTER payloads MUST include:

- four distinct instrument identities
- shared expiry
- valid geometry
- positive net credit
- positive finite max loss
- `DEFINED_RISK` label
- score / confidence / explanation when scoring succeeds

---

## Appendix X — Exit metadata constraints

Exit-window configuration may be copied into metadata for downstream managers.
This module MUST NOT generate exit orders, stop orders, or adjust orders.

---

## Appendix Y — Implementation hazards

| Hazard | Required mitigation |
|---|---|
| Selecting only shorts (strangle drift) | Hard-require four legs and geometry validation. |
| Treating max loss as undefined | Always seal finite max loss and DEFINED_RISK. |
| Using ask-only credit without debit legs | Apply consistent four-leg price policy. |
| Ignoring trend strength | Enforce trend-strength gate before ENTER. |
| Mutating snapshot contracts | Use immutable reads only. |
| Non-deterministic dict iteration | Sort by documented keys. |

---

## Appendix Z — Changelog

| Version | Date | Notes |
|---|---|---|
| 1.0.0 | 2026-08-05 | Initial locked-contract specification for defined-risk iron condor. |

---

## 23. Detailed evaluation algorithm

The evaluation algorithm is the normative control flow for
`IronCondorStrategy._evaluate_result`.

### 23.1 Resolve context

1. If input is `IronCondorContext`, unwrap `StrategyContext` and typed evidence.
2. Else treat input as `StrategyContext` and parse optional tags.
3. Normalize underlying identity to uppercase.
4. Capture `as_of` as the sole temporal authority.

### 23.2 Validate context

1. Reject missing snapshot (`ICS.SNAPSHOT.MISSING`).
2. Reject invalid snapshot identity when `require_valid_snapshot` is true.
3. Reject unsupported underlying (`ICS.UNDERLYING.UNSUPPORTED`).
4. Reject malformed tags that claim to be numeric but are not finite decimals.

### 23.3 Freshness

Compare `snapshot.freshness.age_seconds` with `max_snapshot_age_seconds`.
If greater, reject `ICS.SNAPSHOT.STALE`.

### 23.4 Time window

Convert `as_of` into the configured timezone. If local time is outside
`[start, end)`, abstain `ICS.TIME.OUTSIDE_ENTRY_WINDOW`.

### 23.5 Regime and events

1. Missing regime → reject.
2. Crisis → reject.
3. Unsuitable → abstain.
4. Adverse event → abstain.

### 23.6 Trend strength

1. Missing required trend strength → reject.
2. Non-finite → reject.
3. `>= maximum_trend_strength` → abstain.
4. Else pass and record `ICS.TREND.PASS`.

### 23.7 IV rank

1. Missing required IV rank → reject.
2. Non-finite / out of `[0, 100]` → reject.
3. Below floor → abstain.
4. Else pass and record `ICS.IV_RANK.PASS`.

### 23.8 Select structure

1. Select expiry.
2. Select short put and short call.
3. Select long put and long call.
4. Validate geometry.
5. Validate per-leg liquidity on the final four.
6. Compute credit and risk metrics.
7. Enforce minimum premium and positive max loss.

### 23.9 Score and seal

1. Build factor input bundle with provenance.
2. Call scoring framework.
3. On framework failure, reject `ICS.SCORING.FAILED`.
4. Map recommendation and signal.
5. Optionally publish observational events.
6. Return immutable `IronCondorEvaluationResult`.

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

A RiskProfile that “prefers no premium selling” does not by itself force
REJECT inside this plugin unless a future explicit configuration flag is added
and validated; v1.0 treats it as informational only.

### 24.3 HistoricalData

Injected historical series may be used only for configured fallback IV-rank
derivation. The strategy MUST NOT fetch additional history. Fallback derivation
MUST record provenance distinguishing injected rank from derived rank.

---

## 25. Structure-hint contract for consumers

```text
StructureHint(
  structure_type="iron_condor",
  leg_count=4,
  selection_method="delta_ranked_otm_wings",
  target_delta=float(short_target_delta),
  quantity_hint=1,
  option_types=(PE, PE, CE, CE),
)
```

Exact field names MUST follow the existing `StructureHint` model in
`strategy/signals.py`. Additional leg identity details may be placed in signal
metadata using stable keys:

- `leg0_instrument_id` … `leg3_instrument_id`
- `leg0_strike` … `leg3_strike`
- `leg0_side` … `leg3_side` with values `SELL|BUY`
- `put_wing_width`
- `call_wing_width`
- `net_credit`
- `max_loss`
- `max_loss_label=DEFINED_RISK`

---

## 26. Comparison with Short Strangle

| Dimension | Short Strangle | Iron Condor |
|---|---|---|
| Legs | 2 shorts | 2 shorts + 2 longs |
| Max loss | UNDEFINED_UNLIMITED | DEFINED_RISK finite |
| Long delta targets | N/A | Required |
| Wing geometry | N/A | Mandatory |
| Trend-strength gate | Optional / weaker emphasis | Mandatory by default |
| Execution leg-side map | SELL, SELL | SELL, BUY, SELL, BUY |
| Risk hint | UNDEFINED | DEFINED |

The two strategies remain independent plugins. Neither imports the other for
decision logic. Shared utilities may be duplicated locally when needed to avoid
creating new framework modules.

---

## 27. Explicit prohibition list for implementers

Implementers MUST NOT:

1. Create `strategy/framework_*.py` modules.
2. Redesign `BaseStrategy`.
3. Add broker clients under `strategy/`.
4. Call order placement helpers.
5. Soft-fail geometry errors into ABSTAIN when geometry is invalid.
6. Emit ENTER with fewer than four legs.
7. Emit ENTER with non-positive max loss.
8. Use float binary equality for strike comparisons.
9. Read `datetime.now()` or `time.time()`.
10. Seed randomness for tie-breaks.
11. Persist recommendations to disk.
12. Mutate `tags` or snapshot collections.
13. Treat POP as a hard risk limit.
14. Suppress defined-risk warnings.
15. Depend on dictionary insertion order for ranking.

---

## 28. Recommended unit-test fixture recipe

1. Build a `MarketSnapshot` with finite spot and freshness age within limit.
2. Provide OTM PE and CE contracts across at least two wing distances.
3. Include bid/ask/OI/volume/delta for every candidate.
4. Set `as_of` inside the entry window in the configured timezone.
5. Supply regime `RANGE_BOUND`, IV rank above floor, trend strength below ceiling.
6. Assert ENTER, four strikes, positive credit, positive max loss, DEFINED_RISK.
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
  "strategy_id": "iron_condor",
  "as_of": "2026-08-05T05:00:00+00:00",
  "reasons": ["ICS.GATES.PASS", "ICS.RISK.DEFINED", "ICS.STRUCTURE.FOUR_LEGS"],
  "strike_selection": {
    "underlying": "NIFTY",
    "spot": "24500",
    "expiry": "2026-08-13",
    "long_put_strike": "24000",
    "short_put_strike": "24200",
    "short_call_strike": "24800",
    "long_call_strike": "25000",
    "put_wing_width": "200",
    "call_wing_width": "200"
  },
  "risk_metrics": {
    "net_credit": "45",
    "max_profit": "45",
    "max_loss": "155",
    "max_loss_label": "DEFINED_RISK",
    "probability_of_profit": "0.71",
    "reward_risk_ratio": "0.2903225806451613"
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
| AA-V01 | nominal valid input for configuration and context validation | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V02 | required field absent for configuration and context validation | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V03 | non-finite numeric value for configuration and context validation | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V04 | boundary value equal to minimum for configuration and context validation | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V05 | value one quantum below minimum for configuration and context validation | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V06 | value one quantum above maximum for configuration and context validation | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V07 | timestamp exactly at start boundary for configuration and context validation | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V08 | timestamp exactly at end boundary for configuration and context validation | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V09 | two otherwise equal candidates for configuration and context validation | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V10 | input collection reversed for configuration and context validation | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V11 | unrelated metadata added for configuration and context validation | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V12 | optional metadata absent for configuration and context validation | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V13 | optional metadata malformed for configuration and context validation | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V14 | immutable input reused for configuration and context validation | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V15 | same input evaluated twice for configuration and context validation | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V16 | same input evaluated concurrently for configuration and context validation | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V17 | event sink raises exception for configuration and context validation | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V18 | unknown enum supplied for configuration and context validation | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V19 | unknown payload field supplied for configuration and context validation | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V20 | unsupported underlying supplied for configuration and context validation | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V21 | snapshot id conflicts with contract identity for configuration and context validation | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V22 | selected contract token absent for configuration and context validation | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V23 | symbol absent but immutable instrument id present for configuration and context validation | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V24 | decimal precision exceeds display precision for configuration and context validation | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V25 | negative zero submitted for configuration and context validation | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V26 | large but finite option chain for configuration and context validation | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V27 | duplicate contract id with same facts for configuration and context validation | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V28 | duplicate contract id with conflicting facts for configuration and context validation | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V29 | score framework rejects factor bundle for configuration and context validation | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V30 | score framework returns sealed score for configuration and context validation | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V31 | RiskProfile prefers no entry for configuration and context validation | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V32 | PortfolioSnapshot contains exposure for configuration and context validation | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V33 | HistoricalSeries is injected for configuration and context validation | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V34 | broker adapter is available in process for configuration and context validation | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V35 | environment contains credentials for configuration and context validation | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V36 | order manager exists in composition for configuration and context validation | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V37 | risk engine exists in composition for configuration and context validation | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V38 | trade decision exists downstream for configuration and context validation | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V39 | serialization round trip for configuration and context validation | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AA-V40 | schema version incompatible for configuration and context validation | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AA-ACCEPT-001:** All forty vectors pass without external calls.
**AA-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AA-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AB — Snapshot freshness catalog
The vectors below verify snapshot identity and freshness. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AB-V01 | nominal valid input for snapshot identity and freshness | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V02 | required field absent for snapshot identity and freshness | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V03 | non-finite numeric value for snapshot identity and freshness | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V04 | boundary value equal to minimum for snapshot identity and freshness | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V05 | value one quantum below minimum for snapshot identity and freshness | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V06 | value one quantum above maximum for snapshot identity and freshness | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V07 | timestamp exactly at start boundary for snapshot identity and freshness | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V08 | timestamp exactly at end boundary for snapshot identity and freshness | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V09 | two otherwise equal candidates for snapshot identity and freshness | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V10 | input collection reversed for snapshot identity and freshness | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V11 | unrelated metadata added for snapshot identity and freshness | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V12 | optional metadata absent for snapshot identity and freshness | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V13 | optional metadata malformed for snapshot identity and freshness | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V14 | immutable input reused for snapshot identity and freshness | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V15 | same input evaluated twice for snapshot identity and freshness | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V16 | same input evaluated concurrently for snapshot identity and freshness | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V17 | event sink raises exception for snapshot identity and freshness | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V18 | unknown enum supplied for snapshot identity and freshness | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V19 | unknown payload field supplied for snapshot identity and freshness | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V20 | unsupported underlying supplied for snapshot identity and freshness | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V21 | snapshot id conflicts with contract identity for snapshot identity and freshness | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V22 | selected contract token absent for snapshot identity and freshness | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V23 | symbol absent but immutable instrument id present for snapshot identity and freshness | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V24 | decimal precision exceeds display precision for snapshot identity and freshness | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V25 | negative zero submitted for snapshot identity and freshness | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V26 | large but finite option chain for snapshot identity and freshness | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V27 | duplicate contract id with same facts for snapshot identity and freshness | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V28 | duplicate contract id with conflicting facts for snapshot identity and freshness | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V29 | score framework rejects factor bundle for snapshot identity and freshness | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V30 | score framework returns sealed score for snapshot identity and freshness | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V31 | RiskProfile prefers no entry for snapshot identity and freshness | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V32 | PortfolioSnapshot contains exposure for snapshot identity and freshness | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V33 | HistoricalSeries is injected for snapshot identity and freshness | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V34 | broker adapter is available in process for snapshot identity and freshness | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V35 | environment contains credentials for snapshot identity and freshness | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V36 | order manager exists in composition for snapshot identity and freshness | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V37 | risk engine exists in composition for snapshot identity and freshness | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V38 | trade decision exists downstream for snapshot identity and freshness | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V39 | serialization round trip for snapshot identity and freshness | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AB-V40 | schema version incompatible for snapshot identity and freshness | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AB-ACCEPT-001:** All forty vectors pass without external calls.
**AB-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AB-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AC — Regime gate catalog
The vectors below verify regime and sideways suitability. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AC-V01 | nominal valid input for regime and sideways suitability | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V02 | required field absent for regime and sideways suitability | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V03 | non-finite numeric value for regime and sideways suitability | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V04 | boundary value equal to minimum for regime and sideways suitability | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V05 | value one quantum below minimum for regime and sideways suitability | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V06 | value one quantum above maximum for regime and sideways suitability | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V07 | timestamp exactly at start boundary for regime and sideways suitability | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V08 | timestamp exactly at end boundary for regime and sideways suitability | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V09 | two otherwise equal candidates for regime and sideways suitability | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V10 | input collection reversed for regime and sideways suitability | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V11 | unrelated metadata added for regime and sideways suitability | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V12 | optional metadata absent for regime and sideways suitability | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V13 | optional metadata malformed for regime and sideways suitability | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V14 | immutable input reused for regime and sideways suitability | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V15 | same input evaluated twice for regime and sideways suitability | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V16 | same input evaluated concurrently for regime and sideways suitability | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V17 | event sink raises exception for regime and sideways suitability | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V18 | unknown enum supplied for regime and sideways suitability | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V19 | unknown payload field supplied for regime and sideways suitability | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V20 | unsupported underlying supplied for regime and sideways suitability | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V21 | snapshot id conflicts with contract identity for regime and sideways suitability | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V22 | selected contract token absent for regime and sideways suitability | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V23 | symbol absent but immutable instrument id present for regime and sideways suitability | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V24 | decimal precision exceeds display precision for regime and sideways suitability | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V25 | negative zero submitted for regime and sideways suitability | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V26 | large but finite option chain for regime and sideways suitability | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V27 | duplicate contract id with same facts for regime and sideways suitability | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V28 | duplicate contract id with conflicting facts for regime and sideways suitability | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V29 | score framework rejects factor bundle for regime and sideways suitability | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V30 | score framework returns sealed score for regime and sideways suitability | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V31 | RiskProfile prefers no entry for regime and sideways suitability | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V32 | PortfolioSnapshot contains exposure for regime and sideways suitability | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V33 | HistoricalSeries is injected for regime and sideways suitability | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V34 | broker adapter is available in process for regime and sideways suitability | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V35 | environment contains credentials for regime and sideways suitability | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V36 | order manager exists in composition for regime and sideways suitability | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V37 | risk engine exists in composition for regime and sideways suitability | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V38 | trade decision exists downstream for regime and sideways suitability | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V39 | serialization round trip for regime and sideways suitability | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AC-V40 | schema version incompatible for regime and sideways suitability | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AC-ACCEPT-001:** All forty vectors pass without external calls.
**AC-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AC-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AD — Trend-strength gate catalog
The vectors below verify high trend-strength rejection. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AD-V01 | nominal valid input for high trend-strength rejection | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V02 | required field absent for high trend-strength rejection | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V03 | non-finite numeric value for high trend-strength rejection | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V04 | boundary value equal to minimum for high trend-strength rejection | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V05 | value one quantum below minimum for high trend-strength rejection | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V06 | value one quantum above maximum for high trend-strength rejection | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V07 | timestamp exactly at start boundary for high trend-strength rejection | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V08 | timestamp exactly at end boundary for high trend-strength rejection | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V09 | two otherwise equal candidates for high trend-strength rejection | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V10 | input collection reversed for high trend-strength rejection | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V11 | unrelated metadata added for high trend-strength rejection | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V12 | optional metadata absent for high trend-strength rejection | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V13 | optional metadata malformed for high trend-strength rejection | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V14 | immutable input reused for high trend-strength rejection | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V15 | same input evaluated twice for high trend-strength rejection | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V16 | same input evaluated concurrently for high trend-strength rejection | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V17 | event sink raises exception for high trend-strength rejection | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V18 | unknown enum supplied for high trend-strength rejection | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V19 | unknown payload field supplied for high trend-strength rejection | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V20 | unsupported underlying supplied for high trend-strength rejection | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V21 | snapshot id conflicts with contract identity for high trend-strength rejection | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V22 | selected contract token absent for high trend-strength rejection | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V23 | symbol absent but immutable instrument id present for high trend-strength rejection | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V24 | decimal precision exceeds display precision for high trend-strength rejection | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V25 | negative zero submitted for high trend-strength rejection | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V26 | large but finite option chain for high trend-strength rejection | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V27 | duplicate contract id with same facts for high trend-strength rejection | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V28 | duplicate contract id with conflicting facts for high trend-strength rejection | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V29 | score framework rejects factor bundle for high trend-strength rejection | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V30 | score framework returns sealed score for high trend-strength rejection | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V31 | RiskProfile prefers no entry for high trend-strength rejection | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V32 | PortfolioSnapshot contains exposure for high trend-strength rejection | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V33 | HistoricalSeries is injected for high trend-strength rejection | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V34 | broker adapter is available in process for high trend-strength rejection | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V35 | environment contains credentials for high trend-strength rejection | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V36 | order manager exists in composition for high trend-strength rejection | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V37 | risk engine exists in composition for high trend-strength rejection | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V38 | trade decision exists downstream for high trend-strength rejection | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V39 | serialization round trip for high trend-strength rejection | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AD-V40 | schema version incompatible for high trend-strength rejection | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AD-ACCEPT-001:** All forty vectors pass without external calls.
**AD-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AD-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AE — IV and IV-rank catalog
The vectors below verify IV and IV-rank suitability. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AE-V01 | nominal valid input for IV and IV-rank suitability | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V02 | required field absent for IV and IV-rank suitability | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V03 | non-finite numeric value for IV and IV-rank suitability | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V04 | boundary value equal to minimum for IV and IV-rank suitability | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V05 | value one quantum below minimum for IV and IV-rank suitability | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V06 | value one quantum above maximum for IV and IV-rank suitability | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V07 | timestamp exactly at start boundary for IV and IV-rank suitability | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V08 | timestamp exactly at end boundary for IV and IV-rank suitability | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V09 | two otherwise equal candidates for IV and IV-rank suitability | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V10 | input collection reversed for IV and IV-rank suitability | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V11 | unrelated metadata added for IV and IV-rank suitability | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V12 | optional metadata absent for IV and IV-rank suitability | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V13 | optional metadata malformed for IV and IV-rank suitability | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V14 | immutable input reused for IV and IV-rank suitability | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V15 | same input evaluated twice for IV and IV-rank suitability | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V16 | same input evaluated concurrently for IV and IV-rank suitability | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V17 | event sink raises exception for IV and IV-rank suitability | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V18 | unknown enum supplied for IV and IV-rank suitability | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V19 | unknown payload field supplied for IV and IV-rank suitability | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V20 | unsupported underlying supplied for IV and IV-rank suitability | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V21 | snapshot id conflicts with contract identity for IV and IV-rank suitability | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V22 | selected contract token absent for IV and IV-rank suitability | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V23 | symbol absent but immutable instrument id present for IV and IV-rank suitability | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V24 | decimal precision exceeds display precision for IV and IV-rank suitability | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V25 | negative zero submitted for IV and IV-rank suitability | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V26 | large but finite option chain for IV and IV-rank suitability | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V27 | duplicate contract id with same facts for IV and IV-rank suitability | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V28 | duplicate contract id with conflicting facts for IV and IV-rank suitability | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V29 | score framework rejects factor bundle for IV and IV-rank suitability | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V30 | score framework returns sealed score for IV and IV-rank suitability | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V31 | RiskProfile prefers no entry for IV and IV-rank suitability | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V32 | PortfolioSnapshot contains exposure for IV and IV-rank suitability | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V33 | HistoricalSeries is injected for IV and IV-rank suitability | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V34 | broker adapter is available in process for IV and IV-rank suitability | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V35 | environment contains credentials for IV and IV-rank suitability | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V36 | order manager exists in composition for IV and IV-rank suitability | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V37 | risk engine exists in composition for IV and IV-rank suitability | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V38 | trade decision exists downstream for IV and IV-rank suitability | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V39 | serialization round trip for IV and IV-rank suitability | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AE-V40 | schema version incompatible for IV and IV-rank suitability | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AE-ACCEPT-001:** All forty vectors pass without external calls.
**AE-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AE-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AF — Liquidity gate catalog
The vectors below verify four-leg liquidity thresholds. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AF-V01 | nominal valid input for four-leg liquidity thresholds | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V02 | required field absent for four-leg liquidity thresholds | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V03 | non-finite numeric value for four-leg liquidity thresholds | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V04 | boundary value equal to minimum for four-leg liquidity thresholds | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V05 | value one quantum below minimum for four-leg liquidity thresholds | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V06 | value one quantum above maximum for four-leg liquidity thresholds | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V07 | timestamp exactly at start boundary for four-leg liquidity thresholds | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V08 | timestamp exactly at end boundary for four-leg liquidity thresholds | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V09 | two otherwise equal candidates for four-leg liquidity thresholds | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V10 | input collection reversed for four-leg liquidity thresholds | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V11 | unrelated metadata added for four-leg liquidity thresholds | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V12 | optional metadata absent for four-leg liquidity thresholds | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V13 | optional metadata malformed for four-leg liquidity thresholds | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V14 | immutable input reused for four-leg liquidity thresholds | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V15 | same input evaluated twice for four-leg liquidity thresholds | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V16 | same input evaluated concurrently for four-leg liquidity thresholds | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V17 | event sink raises exception for four-leg liquidity thresholds | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V18 | unknown enum supplied for four-leg liquidity thresholds | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V19 | unknown payload field supplied for four-leg liquidity thresholds | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V20 | unsupported underlying supplied for four-leg liquidity thresholds | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V21 | snapshot id conflicts with contract identity for four-leg liquidity thresholds | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V22 | selected contract token absent for four-leg liquidity thresholds | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V23 | symbol absent but immutable instrument id present for four-leg liquidity thresholds | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V24 | decimal precision exceeds display precision for four-leg liquidity thresholds | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V25 | negative zero submitted for four-leg liquidity thresholds | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V26 | large but finite option chain for four-leg liquidity thresholds | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V27 | duplicate contract id with same facts for four-leg liquidity thresholds | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V28 | duplicate contract id with conflicting facts for four-leg liquidity thresholds | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V29 | score framework rejects factor bundle for four-leg liquidity thresholds | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V30 | score framework returns sealed score for four-leg liquidity thresholds | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V31 | RiskProfile prefers no entry for four-leg liquidity thresholds | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V32 | PortfolioSnapshot contains exposure for four-leg liquidity thresholds | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V33 | HistoricalSeries is injected for four-leg liquidity thresholds | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V34 | broker adapter is available in process for four-leg liquidity thresholds | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V35 | environment contains credentials for four-leg liquidity thresholds | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V36 | order manager exists in composition for four-leg liquidity thresholds | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V37 | risk engine exists in composition for four-leg liquidity thresholds | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V38 | trade decision exists downstream for four-leg liquidity thresholds | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V39 | serialization round trip for four-leg liquidity thresholds | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AF-V40 | schema version incompatible for four-leg liquidity thresholds | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AF-ACCEPT-001:** All forty vectors pass without external calls.
**AF-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AF-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AG — Time-window catalog
The vectors below verify entry and informational exit windows. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AG-V01 | nominal valid input for entry and informational exit windows | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V02 | required field absent for entry and informational exit windows | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V03 | non-finite numeric value for entry and informational exit windows | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V04 | boundary value equal to minimum for entry and informational exit windows | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V05 | value one quantum below minimum for entry and informational exit windows | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V06 | value one quantum above maximum for entry and informational exit windows | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V07 | timestamp exactly at start boundary for entry and informational exit windows | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V08 | timestamp exactly at end boundary for entry and informational exit windows | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V09 | two otherwise equal candidates for entry and informational exit windows | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V10 | input collection reversed for entry and informational exit windows | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V11 | unrelated metadata added for entry and informational exit windows | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V12 | optional metadata absent for entry and informational exit windows | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V13 | optional metadata malformed for entry and informational exit windows | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V14 | immutable input reused for entry and informational exit windows | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V15 | same input evaluated twice for entry and informational exit windows | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V16 | same input evaluated concurrently for entry and informational exit windows | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V17 | event sink raises exception for entry and informational exit windows | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V18 | unknown enum supplied for entry and informational exit windows | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V19 | unknown payload field supplied for entry and informational exit windows | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V20 | unsupported underlying supplied for entry and informational exit windows | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V21 | snapshot id conflicts with contract identity for entry and informational exit windows | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V22 | selected contract token absent for entry and informational exit windows | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V23 | symbol absent but immutable instrument id present for entry and informational exit windows | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V24 | decimal precision exceeds display precision for entry and informational exit windows | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V25 | negative zero submitted for entry and informational exit windows | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V26 | large but finite option chain for entry and informational exit windows | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V27 | duplicate contract id with same facts for entry and informational exit windows | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V28 | duplicate contract id with conflicting facts for entry and informational exit windows | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V29 | score framework rejects factor bundle for entry and informational exit windows | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V30 | score framework returns sealed score for entry and informational exit windows | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V31 | RiskProfile prefers no entry for entry and informational exit windows | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V32 | PortfolioSnapshot contains exposure for entry and informational exit windows | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V33 | HistoricalSeries is injected for entry and informational exit windows | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V34 | broker adapter is available in process for entry and informational exit windows | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V35 | environment contains credentials for entry and informational exit windows | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V36 | order manager exists in composition for entry and informational exit windows | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V37 | risk engine exists in composition for entry and informational exit windows | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V38 | trade decision exists downstream for entry and informational exit windows | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V39 | serialization round trip for entry and informational exit windows | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AG-V40 | schema version incompatible for entry and informational exit windows | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AG-ACCEPT-001:** All forty vectors pass without external calls.
**AG-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AG-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AH — Chain-completeness catalog
The vectors below verify four-leg option-chain completeness. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AH-V01 | nominal valid input for four-leg option-chain completeness | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V02 | required field absent for four-leg option-chain completeness | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V03 | non-finite numeric value for four-leg option-chain completeness | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V04 | boundary value equal to minimum for four-leg option-chain completeness | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V05 | value one quantum below minimum for four-leg option-chain completeness | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V06 | value one quantum above maximum for four-leg option-chain completeness | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V07 | timestamp exactly at start boundary for four-leg option-chain completeness | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V08 | timestamp exactly at end boundary for four-leg option-chain completeness | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V09 | two otherwise equal candidates for four-leg option-chain completeness | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V10 | input collection reversed for four-leg option-chain completeness | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V11 | unrelated metadata added for four-leg option-chain completeness | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V12 | optional metadata absent for four-leg option-chain completeness | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V13 | optional metadata malformed for four-leg option-chain completeness | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V14 | immutable input reused for four-leg option-chain completeness | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V15 | same input evaluated twice for four-leg option-chain completeness | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V16 | same input evaluated concurrently for four-leg option-chain completeness | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V17 | event sink raises exception for four-leg option-chain completeness | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V18 | unknown enum supplied for four-leg option-chain completeness | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V19 | unknown payload field supplied for four-leg option-chain completeness | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V20 | unsupported underlying supplied for four-leg option-chain completeness | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V21 | snapshot id conflicts with contract identity for four-leg option-chain completeness | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V22 | selected contract token absent for four-leg option-chain completeness | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V23 | symbol absent but immutable instrument id present for four-leg option-chain completeness | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V24 | decimal precision exceeds display precision for four-leg option-chain completeness | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V25 | negative zero submitted for four-leg option-chain completeness | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V26 | large but finite option chain for four-leg option-chain completeness | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V27 | duplicate contract id with same facts for four-leg option-chain completeness | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V28 | duplicate contract id with conflicting facts for four-leg option-chain completeness | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V29 | score framework rejects factor bundle for four-leg option-chain completeness | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V30 | score framework returns sealed score for four-leg option-chain completeness | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V31 | RiskProfile prefers no entry for four-leg option-chain completeness | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V32 | PortfolioSnapshot contains exposure for four-leg option-chain completeness | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V33 | HistoricalSeries is injected for four-leg option-chain completeness | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V34 | broker adapter is available in process for four-leg option-chain completeness | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V35 | environment contains credentials for four-leg option-chain completeness | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V36 | order manager exists in composition for four-leg option-chain completeness | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V37 | risk engine exists in composition for four-leg option-chain completeness | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V38 | trade decision exists downstream for four-leg option-chain completeness | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V39 | serialization round trip for four-leg option-chain completeness | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AH-V40 | schema version incompatible for four-leg option-chain completeness | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AH-ACCEPT-001:** All forty vectors pass without external calls.
**AH-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AH-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AI — Expiry selection catalog
The vectors below verify shared expiry selection. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AI-V01 | nominal valid input for shared expiry selection | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V02 | required field absent for shared expiry selection | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V03 | non-finite numeric value for shared expiry selection | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V04 | boundary value equal to minimum for shared expiry selection | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V05 | value one quantum below minimum for shared expiry selection | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V06 | value one quantum above maximum for shared expiry selection | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V07 | timestamp exactly at start boundary for shared expiry selection | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V08 | timestamp exactly at end boundary for shared expiry selection | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V09 | two otherwise equal candidates for shared expiry selection | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V10 | input collection reversed for shared expiry selection | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V11 | unrelated metadata added for shared expiry selection | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V12 | optional metadata absent for shared expiry selection | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V13 | optional metadata malformed for shared expiry selection | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V14 | immutable input reused for shared expiry selection | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V15 | same input evaluated twice for shared expiry selection | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V16 | same input evaluated concurrently for shared expiry selection | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V17 | event sink raises exception for shared expiry selection | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V18 | unknown enum supplied for shared expiry selection | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V19 | unknown payload field supplied for shared expiry selection | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V20 | unsupported underlying supplied for shared expiry selection | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V21 | snapshot id conflicts with contract identity for shared expiry selection | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V22 | selected contract token absent for shared expiry selection | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V23 | symbol absent but immutable instrument id present for shared expiry selection | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V24 | decimal precision exceeds display precision for shared expiry selection | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V25 | negative zero submitted for shared expiry selection | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V26 | large but finite option chain for shared expiry selection | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V27 | duplicate contract id with same facts for shared expiry selection | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V28 | duplicate contract id with conflicting facts for shared expiry selection | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V29 | score framework rejects factor bundle for shared expiry selection | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V30 | score framework returns sealed score for shared expiry selection | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V31 | RiskProfile prefers no entry for shared expiry selection | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V32 | PortfolioSnapshot contains exposure for shared expiry selection | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V33 | HistoricalSeries is injected for shared expiry selection | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V34 | broker adapter is available in process for shared expiry selection | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V35 | environment contains credentials for shared expiry selection | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V36 | order manager exists in composition for shared expiry selection | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V37 | risk engine exists in composition for shared expiry selection | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V38 | trade decision exists downstream for shared expiry selection | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V39 | serialization round trip for shared expiry selection | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AI-V40 | schema version incompatible for shared expiry selection | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AI-ACCEPT-001:** All forty vectors pass without external calls.
**AI-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AI-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AJ — Short-strike selection catalog
The vectors below verify short put and short call selection. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AJ-V01 | nominal valid input for short put and short call selection | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V02 | required field absent for short put and short call selection | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V03 | non-finite numeric value for short put and short call selection | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V04 | boundary value equal to minimum for short put and short call selection | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V05 | value one quantum below minimum for short put and short call selection | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V06 | value one quantum above maximum for short put and short call selection | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V07 | timestamp exactly at start boundary for short put and short call selection | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V08 | timestamp exactly at end boundary for short put and short call selection | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V09 | two otherwise equal candidates for short put and short call selection | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V10 | input collection reversed for short put and short call selection | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V11 | unrelated metadata added for short put and short call selection | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V12 | optional metadata absent for short put and short call selection | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V13 | optional metadata malformed for short put and short call selection | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V14 | immutable input reused for short put and short call selection | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V15 | same input evaluated twice for short put and short call selection | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V16 | same input evaluated concurrently for short put and short call selection | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V17 | event sink raises exception for short put and short call selection | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V18 | unknown enum supplied for short put and short call selection | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V19 | unknown payload field supplied for short put and short call selection | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V20 | unsupported underlying supplied for short put and short call selection | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V21 | snapshot id conflicts with contract identity for short put and short call selection | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V22 | selected contract token absent for short put and short call selection | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V23 | symbol absent but immutable instrument id present for short put and short call selection | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V24 | decimal precision exceeds display precision for short put and short call selection | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V25 | negative zero submitted for short put and short call selection | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V26 | large but finite option chain for short put and short call selection | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V27 | duplicate contract id with same facts for short put and short call selection | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V28 | duplicate contract id with conflicting facts for short put and short call selection | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V29 | score framework rejects factor bundle for short put and short call selection | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V30 | score framework returns sealed score for short put and short call selection | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V31 | RiskProfile prefers no entry for short put and short call selection | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V32 | PortfolioSnapshot contains exposure for short put and short call selection | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V33 | HistoricalSeries is injected for short put and short call selection | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V34 | broker adapter is available in process for short put and short call selection | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V35 | environment contains credentials for short put and short call selection | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V36 | order manager exists in composition for short put and short call selection | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V37 | risk engine exists in composition for short put and short call selection | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V38 | trade decision exists downstream for short put and short call selection | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V39 | serialization round trip for short put and short call selection | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AJ-V40 | schema version incompatible for short put and short call selection | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AJ-ACCEPT-001:** All forty vectors pass without external calls.
**AJ-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AJ-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AK — Long-strike / wing catalog
The vectors below verify long put and long call wing selection. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AK-V01 | nominal valid input for long put and long call wing selection | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V02 | required field absent for long put and long call wing selection | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V03 | non-finite numeric value for long put and long call wing selection | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V04 | boundary value equal to minimum for long put and long call wing selection | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V05 | value one quantum below minimum for long put and long call wing selection | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V06 | value one quantum above maximum for long put and long call wing selection | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V07 | timestamp exactly at start boundary for long put and long call wing selection | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V08 | timestamp exactly at end boundary for long put and long call wing selection | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V09 | two otherwise equal candidates for long put and long call wing selection | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V10 | input collection reversed for long put and long call wing selection | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V11 | unrelated metadata added for long put and long call wing selection | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V12 | optional metadata absent for long put and long call wing selection | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V13 | optional metadata malformed for long put and long call wing selection | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V14 | immutable input reused for long put and long call wing selection | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V15 | same input evaluated twice for long put and long call wing selection | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V16 | same input evaluated concurrently for long put and long call wing selection | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V17 | event sink raises exception for long put and long call wing selection | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V18 | unknown enum supplied for long put and long call wing selection | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V19 | unknown payload field supplied for long put and long call wing selection | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V20 | unsupported underlying supplied for long put and long call wing selection | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V21 | snapshot id conflicts with contract identity for long put and long call wing selection | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V22 | selected contract token absent for long put and long call wing selection | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V23 | symbol absent but immutable instrument id present for long put and long call wing selection | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V24 | decimal precision exceeds display precision for long put and long call wing selection | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V25 | negative zero submitted for long put and long call wing selection | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V26 | large but finite option chain for long put and long call wing selection | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V27 | duplicate contract id with same facts for long put and long call wing selection | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V28 | duplicate contract id with conflicting facts for long put and long call wing selection | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V29 | score framework rejects factor bundle for long put and long call wing selection | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V30 | score framework returns sealed score for long put and long call wing selection | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V31 | RiskProfile prefers no entry for long put and long call wing selection | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V32 | PortfolioSnapshot contains exposure for long put and long call wing selection | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V33 | HistoricalSeries is injected for long put and long call wing selection | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V34 | broker adapter is available in process for long put and long call wing selection | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V35 | environment contains credentials for long put and long call wing selection | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V36 | order manager exists in composition for long put and long call wing selection | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V37 | risk engine exists in composition for long put and long call wing selection | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V38 | trade decision exists downstream for long put and long call wing selection | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V39 | serialization round trip for long put and long call wing selection | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AK-V40 | schema version incompatible for long put and long call wing selection | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AK-ACCEPT-001:** All forty vectors pass without external calls.
**AK-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AK-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AL — Structure geometry catalog
The vectors below verify put_long < put_short < spot < call_short < call_long. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AL-V01 | nominal valid input for put_long < put_short < spot < call_short < call_long | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V02 | required field absent for put_long < put_short < spot < call_short < call_long | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V03 | non-finite numeric value for put_long < put_short < spot < call_short < call_long | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V04 | boundary value equal to minimum for put_long < put_short < spot < call_short < call_long | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V05 | value one quantum below minimum for put_long < put_short < spot < call_short < call_long | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V06 | value one quantum above maximum for put_long < put_short < spot < call_short < call_long | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V07 | timestamp exactly at start boundary for put_long < put_short < spot < call_short < call_long | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V08 | timestamp exactly at end boundary for put_long < put_short < spot < call_short < call_long | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V09 | two otherwise equal candidates for put_long < put_short < spot < call_short < call_long | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V10 | input collection reversed for put_long < put_short < spot < call_short < call_long | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V11 | unrelated metadata added for put_long < put_short < spot < call_short < call_long | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V12 | optional metadata absent for put_long < put_short < spot < call_short < call_long | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V13 | optional metadata malformed for put_long < put_short < spot < call_short < call_long | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V14 | immutable input reused for put_long < put_short < spot < call_short < call_long | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V15 | same input evaluated twice for put_long < put_short < spot < call_short < call_long | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V16 | same input evaluated concurrently for put_long < put_short < spot < call_short < call_long | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V17 | event sink raises exception for put_long < put_short < spot < call_short < call_long | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V18 | unknown enum supplied for put_long < put_short < spot < call_short < call_long | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V19 | unknown payload field supplied for put_long < put_short < spot < call_short < call_long | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V20 | unsupported underlying supplied for put_long < put_short < spot < call_short < call_long | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V21 | snapshot id conflicts with contract identity for put_long < put_short < spot < call_short < call_long | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V22 | selected contract token absent for put_long < put_short < spot < call_short < call_long | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V23 | symbol absent but immutable instrument id present for put_long < put_short < spot < call_short < call_long | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V24 | decimal precision exceeds display precision for put_long < put_short < spot < call_short < call_long | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V25 | negative zero submitted for put_long < put_short < spot < call_short < call_long | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V26 | large but finite option chain for put_long < put_short < spot < call_short < call_long | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V27 | duplicate contract id with same facts for put_long < put_short < spot < call_short < call_long | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V28 | duplicate contract id with conflicting facts for put_long < put_short < spot < call_short < call_long | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V29 | score framework rejects factor bundle for put_long < put_short < spot < call_short < call_long | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V30 | score framework returns sealed score for put_long < put_short < spot < call_short < call_long | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V31 | RiskProfile prefers no entry for put_long < put_short < spot < call_short < call_long | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V32 | PortfolioSnapshot contains exposure for put_long < put_short < spot < call_short < call_long | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V33 | HistoricalSeries is injected for put_long < put_short < spot < call_short < call_long | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V34 | broker adapter is available in process for put_long < put_short < spot < call_short < call_long | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V35 | environment contains credentials for put_long < put_short < spot < call_short < call_long | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V36 | order manager exists in composition for put_long < put_short < spot < call_short < call_long | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V37 | risk engine exists in composition for put_long < put_short < spot < call_short < call_long | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V38 | trade decision exists downstream for put_long < put_short < spot < call_short < call_long | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V39 | serialization round trip for put_long < put_short < spot < call_short < call_long | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AL-V40 | schema version incompatible for put_long < put_short < spot < call_short < call_long | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AL-ACCEPT-001:** All forty vectors pass without external calls.
**AL-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AL-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AM — Premium and credit catalog
The vectors below verify net credit and minimum premium. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AM-V01 | nominal valid input for net credit and minimum premium | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V02 | required field absent for net credit and minimum premium | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V03 | non-finite numeric value for net credit and minimum premium | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V04 | boundary value equal to minimum for net credit and minimum premium | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V05 | value one quantum below minimum for net credit and minimum premium | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V06 | value one quantum above maximum for net credit and minimum premium | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V07 | timestamp exactly at start boundary for net credit and minimum premium | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V08 | timestamp exactly at end boundary for net credit and minimum premium | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V09 | two otherwise equal candidates for net credit and minimum premium | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V10 | input collection reversed for net credit and minimum premium | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V11 | unrelated metadata added for net credit and minimum premium | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V12 | optional metadata absent for net credit and minimum premium | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V13 | optional metadata malformed for net credit and minimum premium | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V14 | immutable input reused for net credit and minimum premium | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V15 | same input evaluated twice for net credit and minimum premium | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V16 | same input evaluated concurrently for net credit and minimum premium | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V17 | event sink raises exception for net credit and minimum premium | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V18 | unknown enum supplied for net credit and minimum premium | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V19 | unknown payload field supplied for net credit and minimum premium | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V20 | unsupported underlying supplied for net credit and minimum premium | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V21 | snapshot id conflicts with contract identity for net credit and minimum premium | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V22 | selected contract token absent for net credit and minimum premium | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V23 | symbol absent but immutable instrument id present for net credit and minimum premium | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V24 | decimal precision exceeds display precision for net credit and minimum premium | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V25 | negative zero submitted for net credit and minimum premium | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V26 | large but finite option chain for net credit and minimum premium | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V27 | duplicate contract id with same facts for net credit and minimum premium | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V28 | duplicate contract id with conflicting facts for net credit and minimum premium | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V29 | score framework rejects factor bundle for net credit and minimum premium | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V30 | score framework returns sealed score for net credit and minimum premium | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V31 | RiskProfile prefers no entry for net credit and minimum premium | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V32 | PortfolioSnapshot contains exposure for net credit and minimum premium | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V33 | HistoricalSeries is injected for net credit and minimum premium | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V34 | broker adapter is available in process for net credit and minimum premium | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V35 | environment contains credentials for net credit and minimum premium | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V36 | order manager exists in composition for net credit and minimum premium | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V37 | risk engine exists in composition for net credit and minimum premium | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V38 | trade decision exists downstream for net credit and minimum premium | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V39 | serialization round trip for net credit and minimum premium | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AM-V40 | schema version incompatible for net credit and minimum premium | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AM-ACCEPT-001:** All forty vectors pass without external calls.
**AM-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AM-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AN — Max profit / max loss catalog
The vectors below verify defined-risk max profit and max loss. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AN-V01 | nominal valid input for defined-risk max profit and max loss | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V02 | required field absent for defined-risk max profit and max loss | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V03 | non-finite numeric value for defined-risk max profit and max loss | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V04 | boundary value equal to minimum for defined-risk max profit and max loss | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V05 | value one quantum below minimum for defined-risk max profit and max loss | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V06 | value one quantum above maximum for defined-risk max profit and max loss | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V07 | timestamp exactly at start boundary for defined-risk max profit and max loss | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V08 | timestamp exactly at end boundary for defined-risk max profit and max loss | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V09 | two otherwise equal candidates for defined-risk max profit and max loss | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V10 | input collection reversed for defined-risk max profit and max loss | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V11 | unrelated metadata added for defined-risk max profit and max loss | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V12 | optional metadata absent for defined-risk max profit and max loss | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V13 | optional metadata malformed for defined-risk max profit and max loss | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V14 | immutable input reused for defined-risk max profit and max loss | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V15 | same input evaluated twice for defined-risk max profit and max loss | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V16 | same input evaluated concurrently for defined-risk max profit and max loss | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V17 | event sink raises exception for defined-risk max profit and max loss | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V18 | unknown enum supplied for defined-risk max profit and max loss | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V19 | unknown payload field supplied for defined-risk max profit and max loss | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V20 | unsupported underlying supplied for defined-risk max profit and max loss | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V21 | snapshot id conflicts with contract identity for defined-risk max profit and max loss | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V22 | selected contract token absent for defined-risk max profit and max loss | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V23 | symbol absent but immutable instrument id present for defined-risk max profit and max loss | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V24 | decimal precision exceeds display precision for defined-risk max profit and max loss | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V25 | negative zero submitted for defined-risk max profit and max loss | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V26 | large but finite option chain for defined-risk max profit and max loss | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V27 | duplicate contract id with same facts for defined-risk max profit and max loss | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V28 | duplicate contract id with conflicting facts for defined-risk max profit and max loss | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V29 | score framework rejects factor bundle for defined-risk max profit and max loss | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V30 | score framework returns sealed score for defined-risk max profit and max loss | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V31 | RiskProfile prefers no entry for defined-risk max profit and max loss | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V32 | PortfolioSnapshot contains exposure for defined-risk max profit and max loss | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V33 | HistoricalSeries is injected for defined-risk max profit and max loss | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V34 | broker adapter is available in process for defined-risk max profit and max loss | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V35 | environment contains credentials for defined-risk max profit and max loss | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V36 | order manager exists in composition for defined-risk max profit and max loss | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V37 | risk engine exists in composition for defined-risk max profit and max loss | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V38 | trade decision exists downstream for defined-risk max profit and max loss | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V39 | serialization round trip for defined-risk max profit and max loss | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AN-V40 | schema version incompatible for defined-risk max profit and max loss | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AN-ACCEPT-001:** All forty vectors pass without external calls.
**AN-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AN-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AO — POP heuristic catalog
The vectors below verify probability-of-profit heuristic. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AO-V01 | nominal valid input for probability-of-profit heuristic | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V02 | required field absent for probability-of-profit heuristic | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V03 | non-finite numeric value for probability-of-profit heuristic | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V04 | boundary value equal to minimum for probability-of-profit heuristic | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V05 | value one quantum below minimum for probability-of-profit heuristic | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V06 | value one quantum above maximum for probability-of-profit heuristic | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V07 | timestamp exactly at start boundary for probability-of-profit heuristic | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V08 | timestamp exactly at end boundary for probability-of-profit heuristic | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V09 | two otherwise equal candidates for probability-of-profit heuristic | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V10 | input collection reversed for probability-of-profit heuristic | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V11 | unrelated metadata added for probability-of-profit heuristic | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V12 | optional metadata absent for probability-of-profit heuristic | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V13 | optional metadata malformed for probability-of-profit heuristic | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V14 | immutable input reused for probability-of-profit heuristic | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V15 | same input evaluated twice for probability-of-profit heuristic | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V16 | same input evaluated concurrently for probability-of-profit heuristic | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V17 | event sink raises exception for probability-of-profit heuristic | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V18 | unknown enum supplied for probability-of-profit heuristic | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V19 | unknown payload field supplied for probability-of-profit heuristic | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V20 | unsupported underlying supplied for probability-of-profit heuristic | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V21 | snapshot id conflicts with contract identity for probability-of-profit heuristic | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V22 | selected contract token absent for probability-of-profit heuristic | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V23 | symbol absent but immutable instrument id present for probability-of-profit heuristic | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V24 | decimal precision exceeds display precision for probability-of-profit heuristic | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V25 | negative zero submitted for probability-of-profit heuristic | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V26 | large but finite option chain for probability-of-profit heuristic | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V27 | duplicate contract id with same facts for probability-of-profit heuristic | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V28 | duplicate contract id with conflicting facts for probability-of-profit heuristic | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V29 | score framework rejects factor bundle for probability-of-profit heuristic | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V30 | score framework returns sealed score for probability-of-profit heuristic | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V31 | RiskProfile prefers no entry for probability-of-profit heuristic | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V32 | PortfolioSnapshot contains exposure for probability-of-profit heuristic | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V33 | HistoricalSeries is injected for probability-of-profit heuristic | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V34 | broker adapter is available in process for probability-of-profit heuristic | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V35 | environment contains credentials for probability-of-profit heuristic | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V36 | order manager exists in composition for probability-of-profit heuristic | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V37 | risk engine exists in composition for probability-of-profit heuristic | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V38 | trade decision exists downstream for probability-of-profit heuristic | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V39 | serialization round trip for probability-of-profit heuristic | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AO-V40 | schema version incompatible for probability-of-profit heuristic | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AO-ACCEPT-001:** All forty vectors pass without external calls.
**AO-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AO-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AP — Scoring integration catalog
The vectors below verify PREMIUM_SELLING factor sealing. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AP-V01 | nominal valid input for PREMIUM_SELLING factor sealing | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V02 | required field absent for PREMIUM_SELLING factor sealing | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V03 | non-finite numeric value for PREMIUM_SELLING factor sealing | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V04 | boundary value equal to minimum for PREMIUM_SELLING factor sealing | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V05 | value one quantum below minimum for PREMIUM_SELLING factor sealing | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V06 | value one quantum above maximum for PREMIUM_SELLING factor sealing | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V07 | timestamp exactly at start boundary for PREMIUM_SELLING factor sealing | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V08 | timestamp exactly at end boundary for PREMIUM_SELLING factor sealing | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V09 | two otherwise equal candidates for PREMIUM_SELLING factor sealing | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V10 | input collection reversed for PREMIUM_SELLING factor sealing | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V11 | unrelated metadata added for PREMIUM_SELLING factor sealing | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V12 | optional metadata absent for PREMIUM_SELLING factor sealing | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V13 | optional metadata malformed for PREMIUM_SELLING factor sealing | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V14 | immutable input reused for PREMIUM_SELLING factor sealing | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V15 | same input evaluated twice for PREMIUM_SELLING factor sealing | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V16 | same input evaluated concurrently for PREMIUM_SELLING factor sealing | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V17 | event sink raises exception for PREMIUM_SELLING factor sealing | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V18 | unknown enum supplied for PREMIUM_SELLING factor sealing | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V19 | unknown payload field supplied for PREMIUM_SELLING factor sealing | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V20 | unsupported underlying supplied for PREMIUM_SELLING factor sealing | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V21 | snapshot id conflicts with contract identity for PREMIUM_SELLING factor sealing | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V22 | selected contract token absent for PREMIUM_SELLING factor sealing | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V23 | symbol absent but immutable instrument id present for PREMIUM_SELLING factor sealing | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V24 | decimal precision exceeds display precision for PREMIUM_SELLING factor sealing | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V25 | negative zero submitted for PREMIUM_SELLING factor sealing | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V26 | large but finite option chain for PREMIUM_SELLING factor sealing | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V27 | duplicate contract id with same facts for PREMIUM_SELLING factor sealing | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V28 | duplicate contract id with conflicting facts for PREMIUM_SELLING factor sealing | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V29 | score framework rejects factor bundle for PREMIUM_SELLING factor sealing | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V30 | score framework returns sealed score for PREMIUM_SELLING factor sealing | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V31 | RiskProfile prefers no entry for PREMIUM_SELLING factor sealing | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V32 | PortfolioSnapshot contains exposure for PREMIUM_SELLING factor sealing | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V33 | HistoricalSeries is injected for PREMIUM_SELLING factor sealing | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V34 | broker adapter is available in process for PREMIUM_SELLING factor sealing | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V35 | environment contains credentials for PREMIUM_SELLING factor sealing | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V36 | order manager exists in composition for PREMIUM_SELLING factor sealing | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V37 | risk engine exists in composition for PREMIUM_SELLING factor sealing | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V38 | trade decision exists downstream for PREMIUM_SELLING factor sealing | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V39 | serialization round trip for PREMIUM_SELLING factor sealing | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AP-V40 | schema version incompatible for PREMIUM_SELLING factor sealing | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AP-ACCEPT-001:** All forty vectors pass without external calls.
**AP-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AP-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AQ — TradingSignal mapping catalog
The vectors below verify ENTER / ABSTAIN / REJECT mapping. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AQ-V01 | nominal valid input for ENTER / ABSTAIN / REJECT mapping | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V02 | required field absent for ENTER / ABSTAIN / REJECT mapping | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V03 | non-finite numeric value for ENTER / ABSTAIN / REJECT mapping | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V04 | boundary value equal to minimum for ENTER / ABSTAIN / REJECT mapping | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V05 | value one quantum below minimum for ENTER / ABSTAIN / REJECT mapping | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V06 | value one quantum above maximum for ENTER / ABSTAIN / REJECT mapping | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V07 | timestamp exactly at start boundary for ENTER / ABSTAIN / REJECT mapping | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V08 | timestamp exactly at end boundary for ENTER / ABSTAIN / REJECT mapping | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V09 | two otherwise equal candidates for ENTER / ABSTAIN / REJECT mapping | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V10 | input collection reversed for ENTER / ABSTAIN / REJECT mapping | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V11 | unrelated metadata added for ENTER / ABSTAIN / REJECT mapping | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V12 | optional metadata absent for ENTER / ABSTAIN / REJECT mapping | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V13 | optional metadata malformed for ENTER / ABSTAIN / REJECT mapping | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V14 | immutable input reused for ENTER / ABSTAIN / REJECT mapping | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V15 | same input evaluated twice for ENTER / ABSTAIN / REJECT mapping | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V16 | same input evaluated concurrently for ENTER / ABSTAIN / REJECT mapping | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V17 | event sink raises exception for ENTER / ABSTAIN / REJECT mapping | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V18 | unknown enum supplied for ENTER / ABSTAIN / REJECT mapping | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V19 | unknown payload field supplied for ENTER / ABSTAIN / REJECT mapping | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V20 | unsupported underlying supplied for ENTER / ABSTAIN / REJECT mapping | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V21 | snapshot id conflicts with contract identity for ENTER / ABSTAIN / REJECT mapping | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V22 | selected contract token absent for ENTER / ABSTAIN / REJECT mapping | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V23 | symbol absent but immutable instrument id present for ENTER / ABSTAIN / REJECT mapping | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V24 | decimal precision exceeds display precision for ENTER / ABSTAIN / REJECT mapping | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V25 | negative zero submitted for ENTER / ABSTAIN / REJECT mapping | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V26 | large but finite option chain for ENTER / ABSTAIN / REJECT mapping | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V27 | duplicate contract id with same facts for ENTER / ABSTAIN / REJECT mapping | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V28 | duplicate contract id with conflicting facts for ENTER / ABSTAIN / REJECT mapping | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V29 | score framework rejects factor bundle for ENTER / ABSTAIN / REJECT mapping | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V30 | score framework returns sealed score for ENTER / ABSTAIN / REJECT mapping | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V31 | RiskProfile prefers no entry for ENTER / ABSTAIN / REJECT mapping | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V32 | PortfolioSnapshot contains exposure for ENTER / ABSTAIN / REJECT mapping | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V33 | HistoricalSeries is injected for ENTER / ABSTAIN / REJECT mapping | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V34 | broker adapter is available in process for ENTER / ABSTAIN / REJECT mapping | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V35 | environment contains credentials for ENTER / ABSTAIN / REJECT mapping | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V36 | order manager exists in composition for ENTER / ABSTAIN / REJECT mapping | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V37 | risk engine exists in composition for ENTER / ABSTAIN / REJECT mapping | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V38 | trade decision exists downstream for ENTER / ABSTAIN / REJECT mapping | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V39 | serialization round trip for ENTER / ABSTAIN / REJECT mapping | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AQ-V40 | schema version incompatible for ENTER / ABSTAIN / REJECT mapping | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AQ-ACCEPT-001:** All forty vectors pass without external calls.
**AQ-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AQ-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AR — Serialization catalog
The vectors below verify versioned canonical JSON. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AR-V01 | nominal valid input for versioned canonical JSON | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V02 | required field absent for versioned canonical JSON | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V03 | non-finite numeric value for versioned canonical JSON | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V04 | boundary value equal to minimum for versioned canonical JSON | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V05 | value one quantum below minimum for versioned canonical JSON | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V06 | value one quantum above maximum for versioned canonical JSON | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V07 | timestamp exactly at start boundary for versioned canonical JSON | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V08 | timestamp exactly at end boundary for versioned canonical JSON | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V09 | two otherwise equal candidates for versioned canonical JSON | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V10 | input collection reversed for versioned canonical JSON | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V11 | unrelated metadata added for versioned canonical JSON | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V12 | optional metadata absent for versioned canonical JSON | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V13 | optional metadata malformed for versioned canonical JSON | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V14 | immutable input reused for versioned canonical JSON | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V15 | same input evaluated twice for versioned canonical JSON | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V16 | same input evaluated concurrently for versioned canonical JSON | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V17 | event sink raises exception for versioned canonical JSON | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V18 | unknown enum supplied for versioned canonical JSON | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V19 | unknown payload field supplied for versioned canonical JSON | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V20 | unsupported underlying supplied for versioned canonical JSON | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V21 | snapshot id conflicts with contract identity for versioned canonical JSON | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V22 | selected contract token absent for versioned canonical JSON | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V23 | symbol absent but immutable instrument id present for versioned canonical JSON | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V24 | decimal precision exceeds display precision for versioned canonical JSON | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V25 | negative zero submitted for versioned canonical JSON | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V26 | large but finite option chain for versioned canonical JSON | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V27 | duplicate contract id with same facts for versioned canonical JSON | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V28 | duplicate contract id with conflicting facts for versioned canonical JSON | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V29 | score framework rejects factor bundle for versioned canonical JSON | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V30 | score framework returns sealed score for versioned canonical JSON | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V31 | RiskProfile prefers no entry for versioned canonical JSON | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V32 | PortfolioSnapshot contains exposure for versioned canonical JSON | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V33 | HistoricalSeries is injected for versioned canonical JSON | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V34 | broker adapter is available in process for versioned canonical JSON | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V35 | environment contains credentials for versioned canonical JSON | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V36 | order manager exists in composition for versioned canonical JSON | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V37 | risk engine exists in composition for versioned canonical JSON | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V38 | trade decision exists downstream for versioned canonical JSON | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V39 | serialization round trip for versioned canonical JSON | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AR-V40 | schema version incompatible for versioned canonical JSON | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AR-ACCEPT-001:** All forty vectors pass without external calls.
**AR-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AR-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AS — Concurrency catalog
The vectors below verify thread-safety and isolation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AS-V01 | nominal valid input for thread-safety and isolation | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V02 | required field absent for thread-safety and isolation | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V03 | non-finite numeric value for thread-safety and isolation | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V04 | boundary value equal to minimum for thread-safety and isolation | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V05 | value one quantum below minimum for thread-safety and isolation | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V06 | value one quantum above maximum for thread-safety and isolation | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V07 | timestamp exactly at start boundary for thread-safety and isolation | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V08 | timestamp exactly at end boundary for thread-safety and isolation | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V09 | two otherwise equal candidates for thread-safety and isolation | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V10 | input collection reversed for thread-safety and isolation | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V11 | unrelated metadata added for thread-safety and isolation | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V12 | optional metadata absent for thread-safety and isolation | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V13 | optional metadata malformed for thread-safety and isolation | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V14 | immutable input reused for thread-safety and isolation | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V15 | same input evaluated twice for thread-safety and isolation | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V16 | same input evaluated concurrently for thread-safety and isolation | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V17 | event sink raises exception for thread-safety and isolation | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V18 | unknown enum supplied for thread-safety and isolation | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V19 | unknown payload field supplied for thread-safety and isolation | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V20 | unsupported underlying supplied for thread-safety and isolation | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V21 | snapshot id conflicts with contract identity for thread-safety and isolation | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V22 | selected contract token absent for thread-safety and isolation | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V23 | symbol absent but immutable instrument id present for thread-safety and isolation | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V24 | decimal precision exceeds display precision for thread-safety and isolation | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V25 | negative zero submitted for thread-safety and isolation | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V26 | large but finite option chain for thread-safety and isolation | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V27 | duplicate contract id with same facts for thread-safety and isolation | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V28 | duplicate contract id with conflicting facts for thread-safety and isolation | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V29 | score framework rejects factor bundle for thread-safety and isolation | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V30 | score framework returns sealed score for thread-safety and isolation | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V31 | RiskProfile prefers no entry for thread-safety and isolation | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V32 | PortfolioSnapshot contains exposure for thread-safety and isolation | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V33 | HistoricalSeries is injected for thread-safety and isolation | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V34 | broker adapter is available in process for thread-safety and isolation | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V35 | environment contains credentials for thread-safety and isolation | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V36 | order manager exists in composition for thread-safety and isolation | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V37 | risk engine exists in composition for thread-safety and isolation | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V38 | trade decision exists downstream for thread-safety and isolation | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V39 | serialization round trip for thread-safety and isolation | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AS-V40 | schema version incompatible for thread-safety and isolation | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AS-ACCEPT-001:** All forty vectors pass without external calls.
**AS-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AS-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AT — Boundary enforcement catalog
The vectors below verify no order, position, or portfolio-risk actions. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AT-V01 | nominal valid input for no order, position, or portfolio-risk actions | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V02 | required field absent for no order, position, or portfolio-risk actions | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V03 | non-finite numeric value for no order, position, or portfolio-risk actions | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V04 | boundary value equal to minimum for no order, position, or portfolio-risk actions | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V05 | value one quantum below minimum for no order, position, or portfolio-risk actions | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V06 | value one quantum above maximum for no order, position, or portfolio-risk actions | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V07 | timestamp exactly at start boundary for no order, position, or portfolio-risk actions | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V08 | timestamp exactly at end boundary for no order, position, or portfolio-risk actions | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V09 | two otherwise equal candidates for no order, position, or portfolio-risk actions | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V10 | input collection reversed for no order, position, or portfolio-risk actions | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V11 | unrelated metadata added for no order, position, or portfolio-risk actions | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V12 | optional metadata absent for no order, position, or portfolio-risk actions | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V13 | optional metadata malformed for no order, position, or portfolio-risk actions | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V14 | immutable input reused for no order, position, or portfolio-risk actions | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V15 | same input evaluated twice for no order, position, or portfolio-risk actions | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V16 | same input evaluated concurrently for no order, position, or portfolio-risk actions | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V17 | event sink raises exception for no order, position, or portfolio-risk actions | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V18 | unknown enum supplied for no order, position, or portfolio-risk actions | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V19 | unknown payload field supplied for no order, position, or portfolio-risk actions | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V20 | unsupported underlying supplied for no order, position, or portfolio-risk actions | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V21 | snapshot id conflicts with contract identity for no order, position, or portfolio-risk actions | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V22 | selected contract token absent for no order, position, or portfolio-risk actions | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V23 | symbol absent but immutable instrument id present for no order, position, or portfolio-risk actions | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V24 | decimal precision exceeds display precision for no order, position, or portfolio-risk actions | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V25 | negative zero submitted for no order, position, or portfolio-risk actions | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V26 | large but finite option chain for no order, position, or portfolio-risk actions | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V27 | duplicate contract id with same facts for no order, position, or portfolio-risk actions | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V28 | duplicate contract id with conflicting facts for no order, position, or portfolio-risk actions | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V29 | score framework rejects factor bundle for no order, position, or portfolio-risk actions | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V30 | score framework returns sealed score for no order, position, or portfolio-risk actions | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V31 | RiskProfile prefers no entry for no order, position, or portfolio-risk actions | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V32 | PortfolioSnapshot contains exposure for no order, position, or portfolio-risk actions | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V33 | HistoricalSeries is injected for no order, position, or portfolio-risk actions | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V34 | broker adapter is available in process for no order, position, or portfolio-risk actions | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V35 | environment contains credentials for no order, position, or portfolio-risk actions | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V36 | order manager exists in composition for no order, position, or portfolio-risk actions | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V37 | risk engine exists in composition for no order, position, or portfolio-risk actions | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V38 | trade decision exists downstream for no order, position, or portfolio-risk actions | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V39 | serialization round trip for no order, position, or portfolio-risk actions | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AT-V40 | schema version incompatible for no order, position, or portfolio-risk actions | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AT-ACCEPT-001:** All forty vectors pass without external calls.
**AT-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AT-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AU — Event-sink catalog
The vectors below verify optional observational events. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AU-V01 | nominal valid input for optional observational events | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V02 | required field absent for optional observational events | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V03 | non-finite numeric value for optional observational events | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V04 | boundary value equal to minimum for optional observational events | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V05 | value one quantum below minimum for optional observational events | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V06 | value one quantum above maximum for optional observational events | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V07 | timestamp exactly at start boundary for optional observational events | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V08 | timestamp exactly at end boundary for optional observational events | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V09 | two otherwise equal candidates for optional observational events | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V10 | input collection reversed for optional observational events | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V11 | unrelated metadata added for optional observational events | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V12 | optional metadata absent for optional observational events | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V13 | optional metadata malformed for optional observational events | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V14 | immutable input reused for optional observational events | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V15 | same input evaluated twice for optional observational events | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V16 | same input evaluated concurrently for optional observational events | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V17 | event sink raises exception for optional observational events | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V18 | unknown enum supplied for optional observational events | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V19 | unknown payload field supplied for optional observational events | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V20 | unsupported underlying supplied for optional observational events | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V21 | snapshot id conflicts with contract identity for optional observational events | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V22 | selected contract token absent for optional observational events | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V23 | symbol absent but immutable instrument id present for optional observational events | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V24 | decimal precision exceeds display precision for optional observational events | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V25 | negative zero submitted for optional observational events | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V26 | large but finite option chain for optional observational events | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V27 | duplicate contract id with same facts for optional observational events | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V28 | duplicate contract id with conflicting facts for optional observational events | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V29 | score framework rejects factor bundle for optional observational events | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V30 | score framework returns sealed score for optional observational events | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V31 | RiskProfile prefers no entry for optional observational events | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V32 | PortfolioSnapshot contains exposure for optional observational events | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V33 | HistoricalSeries is injected for optional observational events | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V34 | broker adapter is available in process for optional observational events | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V35 | environment contains credentials for optional observational events | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V36 | order manager exists in composition for optional observational events | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V37 | risk engine exists in composition for optional observational events | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V38 | trade decision exists downstream for optional observational events | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V39 | serialization round trip for optional observational events | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AU-V40 | schema version incompatible for optional observational events | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AU-ACCEPT-001:** All forty vectors pass without external calls.
**AU-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AU-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AV — Unsupported-underlying catalog
The vectors below verify underlying allow-list. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AV-V01 | nominal valid input for underlying allow-list | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V02 | required field absent for underlying allow-list | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V03 | non-finite numeric value for underlying allow-list | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V04 | boundary value equal to minimum for underlying allow-list | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V05 | value one quantum below minimum for underlying allow-list | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V06 | value one quantum above maximum for underlying allow-list | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V07 | timestamp exactly at start boundary for underlying allow-list | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V08 | timestamp exactly at end boundary for underlying allow-list | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V09 | two otherwise equal candidates for underlying allow-list | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V10 | input collection reversed for underlying allow-list | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V11 | unrelated metadata added for underlying allow-list | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V12 | optional metadata absent for underlying allow-list | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V13 | optional metadata malformed for underlying allow-list | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V14 | immutable input reused for underlying allow-list | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V15 | same input evaluated twice for underlying allow-list | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V16 | same input evaluated concurrently for underlying allow-list | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V17 | event sink raises exception for underlying allow-list | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V18 | unknown enum supplied for underlying allow-list | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V19 | unknown payload field supplied for underlying allow-list | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V20 | unsupported underlying supplied for underlying allow-list | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V21 | snapshot id conflicts with contract identity for underlying allow-list | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V22 | selected contract token absent for underlying allow-list | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V23 | symbol absent but immutable instrument id present for underlying allow-list | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V24 | decimal precision exceeds display precision for underlying allow-list | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V25 | negative zero submitted for underlying allow-list | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V26 | large but finite option chain for underlying allow-list | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V27 | duplicate contract id with same facts for underlying allow-list | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V28 | duplicate contract id with conflicting facts for underlying allow-list | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V29 | score framework rejects factor bundle for underlying allow-list | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V30 | score framework returns sealed score for underlying allow-list | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V31 | RiskProfile prefers no entry for underlying allow-list | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V32 | PortfolioSnapshot contains exposure for underlying allow-list | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V33 | HistoricalSeries is injected for underlying allow-list | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V34 | broker adapter is available in process for underlying allow-list | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V35 | environment contains credentials for underlying allow-list | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V36 | order manager exists in composition for underlying allow-list | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V37 | risk engine exists in composition for underlying allow-list | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V38 | trade decision exists downstream for underlying allow-list | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V39 | serialization round trip for underlying allow-list | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AV-V40 | schema version incompatible for underlying allow-list | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AV-ACCEPT-001:** All forty vectors pass without external calls.
**AV-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AV-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AW — Greeks requirement catalog
The vectors below verify required delta presence. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AW-V01 | nominal valid input for required delta presence | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V02 | required field absent for required delta presence | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V03 | non-finite numeric value for required delta presence | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V04 | boundary value equal to minimum for required delta presence | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V05 | value one quantum below minimum for required delta presence | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V06 | value one quantum above maximum for required delta presence | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V07 | timestamp exactly at start boundary for required delta presence | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V08 | timestamp exactly at end boundary for required delta presence | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V09 | two otherwise equal candidates for required delta presence | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V10 | input collection reversed for required delta presence | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V11 | unrelated metadata added for required delta presence | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V12 | optional metadata absent for required delta presence | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V13 | optional metadata malformed for required delta presence | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V14 | immutable input reused for required delta presence | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V15 | same input evaluated twice for required delta presence | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V16 | same input evaluated concurrently for required delta presence | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V17 | event sink raises exception for required delta presence | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V18 | unknown enum supplied for required delta presence | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V19 | unknown payload field supplied for required delta presence | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V20 | unsupported underlying supplied for required delta presence | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V21 | snapshot id conflicts with contract identity for required delta presence | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V22 | selected contract token absent for required delta presence | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V23 | symbol absent but immutable instrument id present for required delta presence | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V24 | decimal precision exceeds display precision for required delta presence | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V25 | negative zero submitted for required delta presence | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V26 | large but finite option chain for required delta presence | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V27 | duplicate contract id with same facts for required delta presence | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V28 | duplicate contract id with conflicting facts for required delta presence | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V29 | score framework rejects factor bundle for required delta presence | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V30 | score framework returns sealed score for required delta presence | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V31 | RiskProfile prefers no entry for required delta presence | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V32 | PortfolioSnapshot contains exposure for required delta presence | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V33 | HistoricalSeries is injected for required delta presence | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V34 | broker adapter is available in process for required delta presence | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V35 | environment contains credentials for required delta presence | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V36 | order manager exists in composition for required delta presence | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V37 | risk engine exists in composition for required delta presence | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V38 | trade decision exists downstream for required delta presence | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V39 | serialization round trip for required delta presence | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AW-V40 | schema version incompatible for required delta presence | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AW-ACCEPT-001:** All forty vectors pass without external calls.
**AW-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AW-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AX — Wing-width constraint catalog
The vectors below verify configured wing width bounds. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AX-V01 | nominal valid input for configured wing width bounds | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V02 | required field absent for configured wing width bounds | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V03 | non-finite numeric value for configured wing width bounds | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V04 | boundary value equal to minimum for configured wing width bounds | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V05 | value one quantum below minimum for configured wing width bounds | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V06 | value one quantum above maximum for configured wing width bounds | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V07 | timestamp exactly at start boundary for configured wing width bounds | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V08 | timestamp exactly at end boundary for configured wing width bounds | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V09 | two otherwise equal candidates for configured wing width bounds | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V10 | input collection reversed for configured wing width bounds | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V11 | unrelated metadata added for configured wing width bounds | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V12 | optional metadata absent for configured wing width bounds | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V13 | optional metadata malformed for configured wing width bounds | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V14 | immutable input reused for configured wing width bounds | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V15 | same input evaluated twice for configured wing width bounds | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V16 | same input evaluated concurrently for configured wing width bounds | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V17 | event sink raises exception for configured wing width bounds | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V18 | unknown enum supplied for configured wing width bounds | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V19 | unknown payload field supplied for configured wing width bounds | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V20 | unsupported underlying supplied for configured wing width bounds | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V21 | snapshot id conflicts with contract identity for configured wing width bounds | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V22 | selected contract token absent for configured wing width bounds | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V23 | symbol absent but immutable instrument id present for configured wing width bounds | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V24 | decimal precision exceeds display precision for configured wing width bounds | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V25 | negative zero submitted for configured wing width bounds | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V26 | large but finite option chain for configured wing width bounds | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V27 | duplicate contract id with same facts for configured wing width bounds | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V28 | duplicate contract id with conflicting facts for configured wing width bounds | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V29 | score framework rejects factor bundle for configured wing width bounds | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V30 | score framework returns sealed score for configured wing width bounds | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V31 | RiskProfile prefers no entry for configured wing width bounds | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V32 | PortfolioSnapshot contains exposure for configured wing width bounds | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V33 | HistoricalSeries is injected for configured wing width bounds | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V34 | broker adapter is available in process for configured wing width bounds | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V35 | environment contains credentials for configured wing width bounds | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V36 | order manager exists in composition for configured wing width bounds | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V37 | risk engine exists in composition for configured wing width bounds | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V38 | trade decision exists downstream for configured wing width bounds | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V39 | serialization round trip for configured wing width bounds | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AX-V40 | schema version incompatible for configured wing width bounds | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AX-ACCEPT-001:** All forty vectors pass without external calls.
**AX-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AX-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AY — Breakeven catalog
The vectors below verify informational breakeven derivation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AY-V01 | nominal valid input for informational breakeven derivation | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V02 | required field absent for informational breakeven derivation | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V03 | non-finite numeric value for informational breakeven derivation | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V04 | boundary value equal to minimum for informational breakeven derivation | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V05 | value one quantum below minimum for informational breakeven derivation | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V06 | value one quantum above maximum for informational breakeven derivation | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V07 | timestamp exactly at start boundary for informational breakeven derivation | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V08 | timestamp exactly at end boundary for informational breakeven derivation | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V09 | two otherwise equal candidates for informational breakeven derivation | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V10 | input collection reversed for informational breakeven derivation | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V11 | unrelated metadata added for informational breakeven derivation | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V12 | optional metadata absent for informational breakeven derivation | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V13 | optional metadata malformed for informational breakeven derivation | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V14 | immutable input reused for informational breakeven derivation | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V15 | same input evaluated twice for informational breakeven derivation | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V16 | same input evaluated concurrently for informational breakeven derivation | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V17 | event sink raises exception for informational breakeven derivation | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V18 | unknown enum supplied for informational breakeven derivation | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V19 | unknown payload field supplied for informational breakeven derivation | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V20 | unsupported underlying supplied for informational breakeven derivation | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V21 | snapshot id conflicts with contract identity for informational breakeven derivation | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V22 | selected contract token absent for informational breakeven derivation | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V23 | symbol absent but immutable instrument id present for informational breakeven derivation | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V24 | decimal precision exceeds display precision for informational breakeven derivation | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V25 | negative zero submitted for informational breakeven derivation | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V26 | large but finite option chain for informational breakeven derivation | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V27 | duplicate contract id with same facts for informational breakeven derivation | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V28 | duplicate contract id with conflicting facts for informational breakeven derivation | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V29 | score framework rejects factor bundle for informational breakeven derivation | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V30 | score framework returns sealed score for informational breakeven derivation | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V31 | RiskProfile prefers no entry for informational breakeven derivation | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V32 | PortfolioSnapshot contains exposure for informational breakeven derivation | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V33 | HistoricalSeries is injected for informational breakeven derivation | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V34 | broker adapter is available in process for informational breakeven derivation | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V35 | environment contains credentials for informational breakeven derivation | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V36 | order manager exists in composition for informational breakeven derivation | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V37 | risk engine exists in composition for informational breakeven derivation | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V38 | trade decision exists downstream for informational breakeven derivation | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V39 | serialization round trip for informational breakeven derivation | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AY-V40 | schema version incompatible for informational breakeven derivation | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AY-ACCEPT-001:** All forty vectors pass without external calls.
**AY-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AY-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix AZ — Risk-profile hint catalog
The vectors below verify DEFINED risk labeling. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AZ-V01 | nominal valid input for DEFINED risk labeling | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V02 | required field absent for DEFINED risk labeling | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V03 | non-finite numeric value for DEFINED risk labeling | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V04 | boundary value equal to minimum for DEFINED risk labeling | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V05 | value one quantum below minimum for DEFINED risk labeling | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V06 | value one quantum above maximum for DEFINED risk labeling | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V07 | timestamp exactly at start boundary for DEFINED risk labeling | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V08 | timestamp exactly at end boundary for DEFINED risk labeling | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V09 | two otherwise equal candidates for DEFINED risk labeling | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V10 | input collection reversed for DEFINED risk labeling | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V11 | unrelated metadata added for DEFINED risk labeling | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V12 | optional metadata absent for DEFINED risk labeling | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V13 | optional metadata malformed for DEFINED risk labeling | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V14 | immutable input reused for DEFINED risk labeling | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V15 | same input evaluated twice for DEFINED risk labeling | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V16 | same input evaluated concurrently for DEFINED risk labeling | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V17 | event sink raises exception for DEFINED risk labeling | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V18 | unknown enum supplied for DEFINED risk labeling | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V19 | unknown payload field supplied for DEFINED risk labeling | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V20 | unsupported underlying supplied for DEFINED risk labeling | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V21 | snapshot id conflicts with contract identity for DEFINED risk labeling | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V22 | selected contract token absent for DEFINED risk labeling | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V23 | symbol absent but immutable instrument id present for DEFINED risk labeling | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V24 | decimal precision exceeds display precision for DEFINED risk labeling | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V25 | negative zero submitted for DEFINED risk labeling | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V26 | large but finite option chain for DEFINED risk labeling | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V27 | duplicate contract id with same facts for DEFINED risk labeling | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V28 | duplicate contract id with conflicting facts for DEFINED risk labeling | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V29 | score framework rejects factor bundle for DEFINED risk labeling | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V30 | score framework returns sealed score for DEFINED risk labeling | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V31 | RiskProfile prefers no entry for DEFINED risk labeling | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V32 | PortfolioSnapshot contains exposure for DEFINED risk labeling | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V33 | HistoricalSeries is injected for DEFINED risk labeling | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V34 | broker adapter is available in process for DEFINED risk labeling | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V35 | environment contains credentials for DEFINED risk labeling | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V36 | order manager exists in composition for DEFINED risk labeling | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V37 | risk engine exists in composition for DEFINED risk labeling | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V38 | trade decision exists downstream for DEFINED risk labeling | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V39 | serialization round trip for DEFINED risk labeling | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| AZ-V40 | schema version incompatible for DEFINED risk labeling | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**AZ-ACCEPT-001:** All forty vectors pass without external calls.
**AZ-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AZ-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix BA — Portfolio snapshot catalog
The vectors below verify informational portfolio preservation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| BA-V01 | nominal valid input for informational portfolio preservation | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V02 | required field absent for informational portfolio preservation | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V03 | non-finite numeric value for informational portfolio preservation | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V04 | boundary value equal to minimum for informational portfolio preservation | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V05 | value one quantum below minimum for informational portfolio preservation | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V06 | value one quantum above maximum for informational portfolio preservation | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V07 | timestamp exactly at start boundary for informational portfolio preservation | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V08 | timestamp exactly at end boundary for informational portfolio preservation | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V09 | two otherwise equal candidates for informational portfolio preservation | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V10 | input collection reversed for informational portfolio preservation | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V11 | unrelated metadata added for informational portfolio preservation | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V12 | optional metadata absent for informational portfolio preservation | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V13 | optional metadata malformed for informational portfolio preservation | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V14 | immutable input reused for informational portfolio preservation | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V15 | same input evaluated twice for informational portfolio preservation | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V16 | same input evaluated concurrently for informational portfolio preservation | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V17 | event sink raises exception for informational portfolio preservation | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V18 | unknown enum supplied for informational portfolio preservation | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V19 | unknown payload field supplied for informational portfolio preservation | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V20 | unsupported underlying supplied for informational portfolio preservation | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V21 | snapshot id conflicts with contract identity for informational portfolio preservation | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V22 | selected contract token absent for informational portfolio preservation | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V23 | symbol absent but immutable instrument id present for informational portfolio preservation | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V24 | decimal precision exceeds display precision for informational portfolio preservation | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V25 | negative zero submitted for informational portfolio preservation | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V26 | large but finite option chain for informational portfolio preservation | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V27 | duplicate contract id with same facts for informational portfolio preservation | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V28 | duplicate contract id with conflicting facts for informational portfolio preservation | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V29 | score framework rejects factor bundle for informational portfolio preservation | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V30 | score framework returns sealed score for informational portfolio preservation | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V31 | RiskProfile prefers no entry for informational portfolio preservation | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V32 | PortfolioSnapshot contains exposure for informational portfolio preservation | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V33 | HistoricalSeries is injected for informational portfolio preservation | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V34 | broker adapter is available in process for informational portfolio preservation | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V35 | environment contains credentials for informational portfolio preservation | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V36 | order manager exists in composition for informational portfolio preservation | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V37 | risk engine exists in composition for informational portfolio preservation | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V38 | trade decision exists downstream for informational portfolio preservation | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V39 | serialization round trip for informational portfolio preservation | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BA-V40 | schema version incompatible for informational portfolio preservation | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**BA-ACCEPT-001:** All forty vectors pass without external calls.
**BA-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**BA-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix BB — Evaluation-engine compatibility catalog
The vectors below verify BaseStrategy.run contract. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| BB-V01 | nominal valid input for BaseStrategy.run contract | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V02 | required field absent for BaseStrategy.run contract | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V03 | non-finite numeric value for BaseStrategy.run contract | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V04 | boundary value equal to minimum for BaseStrategy.run contract | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V05 | value one quantum below minimum for BaseStrategy.run contract | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V06 | value one quantum above maximum for BaseStrategy.run contract | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V07 | timestamp exactly at start boundary for BaseStrategy.run contract | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V08 | timestamp exactly at end boundary for BaseStrategy.run contract | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V09 | two otherwise equal candidates for BaseStrategy.run contract | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V10 | input collection reversed for BaseStrategy.run contract | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V11 | unrelated metadata added for BaseStrategy.run contract | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V12 | optional metadata absent for BaseStrategy.run contract | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V13 | optional metadata malformed for BaseStrategy.run contract | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V14 | immutable input reused for BaseStrategy.run contract | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V15 | same input evaluated twice for BaseStrategy.run contract | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V16 | same input evaluated concurrently for BaseStrategy.run contract | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V17 | event sink raises exception for BaseStrategy.run contract | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V18 | unknown enum supplied for BaseStrategy.run contract | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V19 | unknown payload field supplied for BaseStrategy.run contract | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V20 | unsupported underlying supplied for BaseStrategy.run contract | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V21 | snapshot id conflicts with contract identity for BaseStrategy.run contract | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V22 | selected contract token absent for BaseStrategy.run contract | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V23 | symbol absent but immutable instrument id present for BaseStrategy.run contract | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V24 | decimal precision exceeds display precision for BaseStrategy.run contract | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V25 | negative zero submitted for BaseStrategy.run contract | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V26 | large but finite option chain for BaseStrategy.run contract | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V27 | duplicate contract id with same facts for BaseStrategy.run contract | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V28 | duplicate contract id with conflicting facts for BaseStrategy.run contract | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V29 | score framework rejects factor bundle for BaseStrategy.run contract | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V30 | score framework returns sealed score for BaseStrategy.run contract | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V31 | RiskProfile prefers no entry for BaseStrategy.run contract | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V32 | PortfolioSnapshot contains exposure for BaseStrategy.run contract | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V33 | HistoricalSeries is injected for BaseStrategy.run contract | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V34 | broker adapter is available in process for BaseStrategy.run contract | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V35 | environment contains credentials for BaseStrategy.run contract | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V36 | order manager exists in composition for BaseStrategy.run contract | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V37 | risk engine exists in composition for BaseStrategy.run contract | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V38 | trade decision exists downstream for BaseStrategy.run contract | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V39 | serialization round trip for BaseStrategy.run contract | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BB-V40 | schema version incompatible for BaseStrategy.run contract | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**BB-ACCEPT-001:** All forty vectors pass without external calls.
**BB-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**BB-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix BC — Historical-series fallback catalog
The vectors below verify injected historical-series fallback behavior. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| BC-V01 | nominal valid input for injected historical-series fallback behavior | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V02 | required field absent for injected historical-series fallback behavior | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V03 | non-finite numeric value for injected historical-series fallback behavior | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V04 | boundary value equal to minimum for injected historical-series fallback behavior | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V05 | value one quantum below minimum for injected historical-series fallback behavior | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V06 | value one quantum above maximum for injected historical-series fallback behavior | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V07 | timestamp exactly at start boundary for injected historical-series fallback behavior | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V08 | timestamp exactly at end boundary for injected historical-series fallback behavior | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V09 | two otherwise equal candidates for injected historical-series fallback behavior | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V10 | input collection reversed for injected historical-series fallback behavior | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V11 | unrelated metadata added for injected historical-series fallback behavior | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V12 | optional metadata absent for injected historical-series fallback behavior | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V13 | optional metadata malformed for injected historical-series fallback behavior | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V14 | immutable input reused for injected historical-series fallback behavior | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V15 | same input evaluated twice for injected historical-series fallback behavior | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V16 | same input evaluated concurrently for injected historical-series fallback behavior | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V17 | event sink raises exception for injected historical-series fallback behavior | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V18 | unknown enum supplied for injected historical-series fallback behavior | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V19 | unknown payload field supplied for injected historical-series fallback behavior | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V20 | unsupported underlying supplied for injected historical-series fallback behavior | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V21 | snapshot id conflicts with contract identity for injected historical-series fallback behavior | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V22 | selected contract token absent for injected historical-series fallback behavior | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V23 | symbol absent but immutable instrument id present for injected historical-series fallback behavior | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V24 | decimal precision exceeds display precision for injected historical-series fallback behavior | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V25 | negative zero submitted for injected historical-series fallback behavior | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V26 | large but finite option chain for injected historical-series fallback behavior | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V27 | duplicate contract id with same facts for injected historical-series fallback behavior | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V28 | duplicate contract id with conflicting facts for injected historical-series fallback behavior | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V29 | score framework rejects factor bundle for injected historical-series fallback behavior | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V30 | score framework returns sealed score for injected historical-series fallback behavior | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V31 | RiskProfile prefers no entry for injected historical-series fallback behavior | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V32 | PortfolioSnapshot contains exposure for injected historical-series fallback behavior | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V33 | HistoricalSeries is injected for injected historical-series fallback behavior | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V34 | broker adapter is available in process for injected historical-series fallback behavior | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V35 | environment contains credentials for injected historical-series fallback behavior | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V36 | order manager exists in composition for injected historical-series fallback behavior | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V37 | risk engine exists in composition for injected historical-series fallback behavior | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V38 | trade decision exists downstream for injected historical-series fallback behavior | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V39 | serialization round trip for injected historical-series fallback behavior | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BC-V40 | schema version incompatible for injected historical-series fallback behavior | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**BC-ACCEPT-001:** All forty vectors pass without external calls.
**BC-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**BC-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.

## Appendix BD — Performance catalog
The vectors below verify complexity and benchmark acceptance vectors. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| BD-V01 | nominal valid input for complexity and benchmark acceptance vectors | expected pass and sealed evidence; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V02 | required field absent for complexity and benchmark acceptance vectors | expected reject with the documented ICS code; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V03 | non-finite numeric value for complexity and benchmark acceptance vectors | expected reject before scoring; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V04 | boundary value equal to minimum for complexity and benchmark acceptance vectors | expected inclusive pass where specified; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V05 | value one quantum below minimum for complexity and benchmark acceptance vectors | expected abstain where the condition is suitability; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V06 | value one quantum above maximum for complexity and benchmark acceptance vectors | expected abstain or reject under the named rule; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V07 | timestamp exactly at start boundary for complexity and benchmark acceptance vectors | expected entry-window inclusion; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V08 | timestamp exactly at end boundary for complexity and benchmark acceptance vectors | expected entry-window abstention; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V09 | two otherwise equal candidates for complexity and benchmark acceptance vectors | expected documented lexical tie-break; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V10 | input collection reversed for complexity and benchmark acceptance vectors | expected byte-equivalent result; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V11 | unrelated metadata added for complexity and benchmark acceptance vectors | expected unchanged selection and score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V12 | optional metadata absent for complexity and benchmark acceptance vectors | expected documented fallback or no-op; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V13 | optional metadata malformed for complexity and benchmark acceptance vectors | expected safe reject without external access; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V14 | immutable input reused for complexity and benchmark acceptance vectors | expected no mutation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V15 | same input evaluated twice for complexity and benchmark acceptance vectors | expected identical canonical JSON; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V16 | same input evaluated concurrently for complexity and benchmark acceptance vectors | expected isolated immutable results; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V17 | event sink raises exception for complexity and benchmark acceptance vectors | expected sealed result and isolated sink failure; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V18 | unknown enum supplied for complexity and benchmark acceptance vectors | expected reject during validation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V19 | unknown payload field supplied for complexity and benchmark acceptance vectors | expected reader-policy behavior; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V20 | unsupported underlying supplied for complexity and benchmark acceptance vectors | expected explicit rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V21 | snapshot id conflicts with contract identity for complexity and benchmark acceptance vectors | expected reject before selection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V22 | selected contract token absent for complexity and benchmark acceptance vectors | expected valid recommendation with null token; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V23 | symbol absent but immutable instrument id present for complexity and benchmark acceptance vectors | expected valid recommendation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V24 | decimal precision exceeds display precision for complexity and benchmark acceptance vectors | expected deterministic sealing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V25 | negative zero submitted for complexity and benchmark acceptance vectors | expected normalization or strict rejection by field contract; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V26 | large but finite option chain for complexity and benchmark acceptance vectors | expected bounded deterministic processing; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V27 | duplicate contract id with same facts for complexity and benchmark acceptance vectors | expected deterministic deduplication policy; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V28 | duplicate contract id with conflicting facts for complexity and benchmark acceptance vectors | expected chain rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V29 | score framework rejects factor bundle for complexity and benchmark acceptance vectors | expected ICS.SCORING.FAILED reject; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V30 | score framework returns sealed score for complexity and benchmark acceptance vectors | expected embedded immutable score; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V31 | RiskProfile prefers no entry for complexity and benchmark acceptance vectors | expected informational preservation, not enforcement; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V32 | PortfolioSnapshot contains exposure for complexity and benchmark acceptance vectors | expected no portfolio mutation or calculation; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V33 | HistoricalSeries is injected for complexity and benchmark acceptance vectors | expected no data fetch and explicit provenance; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V34 | broker adapter is available in process for complexity and benchmark acceptance vectors | expected strategy not to call it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V35 | environment contains credentials for complexity and benchmark acceptance vectors | expected strategy not to read them; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V36 | order manager exists in composition for complexity and benchmark acceptance vectors | expected strategy not to reference it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V37 | risk engine exists in composition for complexity and benchmark acceptance vectors | expected strategy not to invoke it; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V38 | trade decision exists downstream for complexity and benchmark acceptance vectors | expected strategy not to self-select; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V39 | serialization round trip for complexity and benchmark acceptance vectors | expected invariant-preserving reconstructed model; preserve BOUNDARY-ICS and deterministic audit reasons. |
| BD-V40 | schema version incompatible for complexity and benchmark acceptance vectors | expected deserialization rejection; preserve BOUNDARY-ICS and deterministic audit reasons. |

**BD-ACCEPT-001:** All forty vectors pass without external calls.
**BD-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**BD-ACCEPT-003:** An `ENTER` result, if produced, retains `DEFINED_RISK` finite max-loss semantics.
