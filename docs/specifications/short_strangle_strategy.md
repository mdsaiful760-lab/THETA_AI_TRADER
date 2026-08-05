# Short Strangle Strategy — Software Engineering Specification

| Field | Value |
|---|---|
| Module | `strategy/short_strangle_strategy.py` |
| Document version | `1.0.0` |
| Status | Implementation contract |
| Owner | THETA AI TRADER Core Platform |
| Last updated | 2026-08-05 |
| Strategy identifier | `short_strangle` |
| Strategy family | `short_strangle` |
| Risk profile | Naked premium selling; loss is unbounded/undefined |

---

## 1. Purpose

`strategy/short_strangle_strategy.py` is the deterministic, read-only Short
Strangle strategy plugin for THETA AI TRADER v1.0.

It answers the following bounded question:

> Given an injected `MarketSnapshot`, optional historical and portfolio
> context, an optional risk-profile preference, and an immutable
> `ShortStrangleConfiguration`, is a naked short strangle suitable now; which
> call and put strikes are candidates; what are its estimated credit,
> probability of profit, deltas, and undefined-risk warning; and what
> structured recommendation and score should downstream decision systems see?

The answer is analytical evidence only. It is not a trade approval, order, risk
reservation, position update, or broker instruction.

### 1.1 Gap filled

| Component | Contractual boundary |
|---|---|
| `strategy/base_strategy.py` | Defines the common plugin contract and invokes strategy logic through `run(StrategyContext)`. |
| `strategy/strategy_evaluation_engine.py` | Invokes this plugin with context, collects reports, and compares strategy reports. |
| This module | Evaluates short-strangle suitability and emits an immutable signal and recommendation. |
| `strategy/strategy_scoring_framework.py` | Seals normalized factor inputs into `StrategyScore`, `ConfidenceReport`, and `StrategyExplanation`. |
| Trade Decision Engine | Selects among evaluation reports and independently approves, declines, or defers a possible trade. |
| Risk Engine | Enforces authoritative risk, margin, concentration, event, and portfolio constraints. |
| Execution / Order Manager | Builds, routes, modifies, and cancels approved orders. |

### 1.2 Frozen pipeline

```text
MarketSnapshot (+ optional HistoricalData / PortfolioSnapshot / RiskProfile)
  → ShortStrangleStrategy (BaseStrategy plugin)
  → TradingSignal + ShortStrangleRecommendation
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

- **BOUNDARY-SSS-001:** The strategy MUST NOT place an order.
- **BOUNDARY-SSS-002:** The strategy MUST NOT modify or cancel an order.
- **BOUNDARY-SSS-003:** The strategy MUST NOT create, reconcile, or manage a position.
- **BOUNDARY-SSS-004:** The strategy MUST NOT calculate authoritative portfolio risk.
- **BOUNDARY-SSS-005:** The strategy MUST NOT calculate authoritative margin.
- **BOUNDARY-SSS-006:** The strategy MUST NOT calculate position size.
- **BOUNDARY-SSS-007:** The strategy MUST NOT call a broker API.
- **BOUNDARY-SSS-008:** The strategy MUST NOT import or call `kiteconnect`.
- **BOUNDARY-SSS-009:** The strategy MUST NOT fetch a live quote or option chain.
- **BOUNDARY-SSS-010:** The strategy MUST NOT subscribe to a websocket.
- **BOUNDARY-SSS-011:** The strategy MUST NOT load `.env`, files, or credentials.
- **BOUNDARY-SSS-012:** The strategy MUST NOT replace the Evaluation Engine.
- **BOUNDARY-SSS-013:** The strategy MUST NOT replace the Trade Decision Engine.
- **BOUNDARY-SSS-014:** The strategy MUST NOT replace Risk or Execution.
- **BOUNDARY-SSS-015:** The strategy MUST NOT mutate `MarketSnapshot`.
- **BOUNDARY-SSS-016:** The strategy MUST NOT mutate `PortfolioSnapshot`.
- **BOUNDARY-SSS-017:** The strategy MUST NOT mutate context metadata.
- **BOUNDARY-SSS-018:** The strategy MUST NOT retain mutable caller-owned data.
- **BOUNDARY-SSS-019:** The strategy MUST NOT silently infer unavailable Greeks.
- **BOUNDARY-SSS-020:** The strategy MUST NOT represent a heuristic POP as a guarantee.
- **BOUNDARY-SSS-021:** The strategy MUST NOT cap naked-strangle loss in its output.
- **BOUNDARY-SSS-022:** The strategy MUST NOT suppress the undefined-risk warning.
- **BOUNDARY-SSS-023:** The strategy MUST NOT use wall-clock time except injected context time.
- **BOUNDARY-SSS-024:** The strategy MUST NOT use randomness.
- **BOUNDARY-SSS-025:** The strategy MUST NOT publish a signal with invalid evidence.

### 1.4 Goals

1. Provide a single deterministic implementation of short-strangle suitability.
2. Prefer range-bound regimes with elevated implied volatility.
3. Reject trending, crisis, stale, incomplete, and illiquid conditions.
4. Select OTM call and put candidates by target-delta proximity.
5. Explain every recommendation and every abstention.
6. Produce immutable artifacts that downstream components can serialize safely.
7. Integrate with the shared scoring framework without reimplementing scoring.
8. Make undefined naked risk unambiguous to every consumer.
9. Permit deterministic unit tests with no broker or network dependency.
10. Preserve the locked platform pipeline.

### 1.5 Success criteria

- Equivalent valid inputs yield equivalent sealed outputs across runs and threads.
- Each `ENTER` recommendation contains exactly one call and one put short leg.
- Each `ABSTAIN` or `REJECT` recommendation contains a stable machine code and reason.
- Candidate ranking is nearest delta, then tighter spread, then higher OI.
- Missing mandatory inputs fail closed before an entry recommendation.
- No production code path imports broker, websocket, credential, or environment facilities.
- Every entry artifact states that max loss is `UNDEFINED_UNLIMITED`.
- Unit coverage of `strategy/short_strangle_strategy.py` is at least 95%.

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
| R7 | Inspect injected regime evidence. |
| R8 | Inspect injected IV and IV-rank evidence. |
| R9 | Use injected history only when configuration permits fallback derivation. |
| R10 | Validate option-chain completeness. |
| R11 | Filter expired, malformed, and non-OTM option contracts. |
| R12 | Filter contracts outside liquidity thresholds. |
| R13 | Select a compatible expiry deterministically. |
| R14 | Select the short call from OTM call candidates. |
| R15 | Select the short put from OTM put candidates. |
| R16 | Calculate configured premium using MID or ASK policy. |
| R17 | Calculate a documented POP heuristic. |
| R18 | Produce net-credit and max-profit metrics. |
| R19 | State theoretical max loss as undefined/unlimited. |
| R20 | Produce scoring-factor inputs with provenance. |
| R21 | Call `StrategyScoringFramework.score()` only after gates pass or with explicit abstention evidence. |
| R22 | Map sealed scoring artifacts to `TradingSignal`. |
| R23 | Include a two-leg short structure hint for entries. |
| R24 | Include stable, ordered explanatory reasons. |
| R25 | Produce an immutable plugin-internal evaluation artifact. |
| R26 | Serialize public models using versioned canonical payloads. |
| R27 | Reject invalid deserialized payloads. |
| R28 | Support optional observational event publication through an injected sink. |
| R29 | Preserve an informational risk-profile hint without enforcing it. |
| R30 | Preserve an informational portfolio snapshot without mutating or pricing it. |
| R31 | Provide deterministic ranking keys for evaluation-engine consumption. |
| R32 | Make all gate outcomes auditable with identifiers and observed values. |
| R33 | Support safe empty-chain abstention. |
| R34 | Keep strategy state stateless and thread-safe. |

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

---

## 4. Strategy identity and registry metadata

The registration key is exactly `short_strangle`. It is lowercase, stable, and
not user-configurable.

| Metadata field | Required value |
|---|---|
| `strategy_id` | `short_strangle` |
| `display_name` | `Short Strangle` |
| `family` | `short_strangle` |
| `version` | `1.0.0` |
| `direction` | `NEUTRAL` |
| `risk_profile_hint` | `UNDEFINED_UNLIMITED` |
| `required_structure` | Two short option legs |
| `scoring_profile_default` | `PREMIUM_SELLING` |
| `supports_direct_execution` | `false` |
| `supports_position_management` | `false` |

**REG-SSS-001:** `strategy/registry.py` MUST register the class under
`short_strangle`.

**REG-SSS-002:** Duplicate registration MUST fail at registry construction.

**REG-SSS-003:** The registration factory MUST receive immutable configuration
and optional injected collaborators only.

**REG-SSS-004:** Registry metadata MUST advertise undefined/unlimited risk.

**REG-SSS-005:** A registry consumer MUST NOT infer that registration authorizes
trading.

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
5. IV and IV-rank suitability.
6. Option-chain completeness.
7. Liquidity.
8. Expiry and strike selection.
9. Premium and metric validation.
10. Scoring and signal sealing.

### 5.2 Regime gate

| Regime tag | Default outcome | Rule |
|---|---|---|
| `RANGE_BOUND` | PASS | Preferred when other gates pass. |
| `MEAN_REVERTING` | PASS | Permitted with no crisis flag. |
| `NEUTRAL` | ABSTAIN | Insufficient positive evidence by default. |
| `TRENDING_UP` | ABSTAIN | Directional trend is unsuitable for naked neutrality. |
| `TRENDING_DOWN` | ABSTAIN | Directional trend is unsuitable for naked neutrality. |
| `BREAKOUT` | ABSTAIN | Expansion risk is unsuitable. |
| `HIGH_VOLATILITY_CRISIS` | REJECT | Crisis condition violates entry policy. |
| absent | REJECT | Required regime evidence is absent. |

- **GATE-SSS-001:** Only explicit regime tags may be used.
- **GATE-SSS-002:** A regime score cannot overturn an unsuitable regime tag.
- **GATE-SSS-003:** A crisis tag is an immediate reject.
- **GATE-SSS-004:** A contradictory set of supplied tags is a reject.
- **GATE-SSS-005:** Regime evidence must identify its observation timestamp.

### 5.3 IV and IV-rank gate

`iv_rank` is a supplied bounded percentile in `[0, 100]`. It is not inferred
from a broker call. If injected `HistoricalSeries` is used to calculate an
allowed fallback rank, the complete series must already be in the context.

- **GATE-SSS-010:** `iv_rank >= minimum_iv_rank` is mandatory.
- **GATE-SSS-011:** Non-finite IV or IV rank is rejected.
- **GATE-SSS-012:** Missing IV rank is rejected when `require_iv_rank` is true.
- **GATE-SSS-013:** A fallback rank may be used only when configuration enables it.
- **GATE-SSS-014:** A fallback requires at least `iv_rank_lookback_observations`.
- **GATE-SSS-015:** IV rank is a suitability signal, never a profitability guarantee.

### 5.4 Liquidity gate

Every selected leg must independently pass liquidity. The pair passes only when
both legs pass.

| Metric | Default interpretation |
|---|---|
| Bid | Must be finite and non-negative. |
| Ask | Must be finite, positive, and at least bid. |
| Absolute spread | `ask - bid <= maximum_spread_width`. |
| Relative spread | `(ask - bid) / midpoint <= maximum_relative_spread_width`. |
| Open interest | `oi >= minimum_open_interest`. |
| Volume | `volume >= minimum_volume`. |
| Quote time | Within configured quote-age threshold if available. |

- **GATE-SSS-020:** Missing bid or ask rejects the affected contract.
- **GATE-SSS-021:** Crossed quotes reject the affected contract.
- **GATE-SSS-022:** Zero or negative midpoint rejects the affected contract.
- **GATE-SSS-023:** OI below the floor abstains for that candidate.
- **GATE-SSS-024:** Volume below the floor abstains for that candidate.
- **GATE-SSS-025:** Spread above either enabled limit abstains for that candidate.
- **GATE-SSS-026:** An absent optional OI field rejects when OI is required.
- **GATE-SSS-027:** An absent optional volume field rejects when volume is required.

### 5.5 Time-window gate

The configuration contains explicit exchange-local entry and informational exit
windows. Context supplies the observed timestamp and exchange timezone.

- **GATE-SSS-030:** Entry is permitted only inside an inclusive start and exclusive end interval.
- **GATE-SSS-031:** The exit window is copied to metadata and is never acted on.
- **GATE-SSS-032:** Missing timezone data rejects a time-window evaluation.
- **GATE-SSS-033:** A timestamp on the end boundary abstains.
- **GATE-SSS-034:** Cross-midnight windows are rejected in v1.0.
- **GATE-SSS-035:** The plugin never waits for a future window.

### 5.6 Chain-completeness gate

- **GATE-SSS-040:** Underlying spot must be finite and strictly positive.
- **GATE-SSS-041:** At least one eligible OTM call candidate must exist.
- **GATE-SSS-042:** At least one eligible OTM put candidate must exist.
- **GATE-SSS-043:** Call and put candidates must share a selected expiry.
- **GATE-SSS-044:** Required Greek fields must be present for every selected leg.
- **GATE-SSS-045:** Contract strike, expiry, type, and quote identity must agree.
- **GATE-SSS-046:** Duplicate instrument identifiers with conflicting facts reject the snapshot.

---

## 6. Strike selection algorithm

The algorithm selects one short OTM CE and one short OTM PE for a single expiry.
It is deterministic and never calls a broker or market-data service.

### 6.1 Definitions

| Term | Definition |
|---|---|
| OTM call | Contract with `strike > spot`. |
| OTM put | Contract with `strike < spot`. |
| Call target | `abs(config.call_target_delta)`; positive canonical magnitude. |
| Put target | `abs(config.put_target_delta)`; positive canonical magnitude. |
| Delta error | `abs(abs(contract.delta) - target_delta)`. |
| Eligible contract | Correct type, selected expiry, OTM, valid quote, liquidity-pass, and valid required Greek. |

### 6.2 Expiry selection

1. Group valid contracts by expiry.
2. Exclude expiries earlier than the context observation date.
3. Exclude expiries outside configured DTE bounds.
4. Retain expiries containing at least one eligible CE and PE.
5. Choose the expiry with the lowest non-negative DTE.
6. If DTE ties, choose the earlier normalized expiry timestamp.
7. If normalized expiry ties, choose lexicographically smallest expiry identifier.

- **STRIKE-SSS-001:** Expiry selection is completed before leg selection.
- **STRIKE-SSS-002:** A same-day expiry is permitted only if `minimum_dte == 0`.
- **STRIKE-SSS-003:** Expired contracts are never candidates.
- **STRIKE-SSS-004:** CE and PE MUST use the same expiry.

### 6.3 Candidate ranking

For each option side, sort eligible candidates by this ascending tuple:

```text
(
  abs(abs(delta) - target_delta),
  relative_spread,
  -open_interest,
  -volume,
  strike,
  instrument_id,
)
```

Missing OI or volume is not converted to zero when either is required; such a
candidate was already rejected. When not required, missing values rank after
present values using a documented sentinel.

- **STRIKE-SSS-010:** Delta error must not exceed `delta_selection_tolerance`.
- **STRIKE-SSS-011:** The selected CE must be OTM at evaluation spot.
- **STRIKE-SSS-012:** The selected PE must be OTM at evaluation spot.
- **STRIKE-SSS-013:** Equal ranking tuples are resolved by `instrument_id`.
- **STRIKE-SSS-014:** Floating values are compared as normalized decimals.
- **STRIKE-SSS-015:** The input option-chain order must not influence selection.

### 6.4 Pseudocode

```python
def choose_leg(
    contracts: tuple[OptionContract, ...],
    side: OptionType,
    spot: Decimal,
    expiry: date,
    target_delta: Decimal,
    config: ShortStrangleConfiguration,
) -> OptionContract | GateFailure:
    candidates = [
        contract
        for contract in contracts
        if is_eligible(contract, side, spot, expiry, config)
        and abs(abs(contract.delta) - target_delta)
        <= config.delta_selection_tolerance
    ]
    if not candidates:
        return GateFailure("SSS.STRIKE.NO_ELIGIBLE_CANDIDATE")
    return min(candidates, key=candidate_rank_key)
```

### 6.5 Pair validation

- **STRIKE-SSS-020:** Selected CE strike MUST exceed spot.
- **STRIKE-SSS-021:** Selected PE strike MUST be below spot.
- **STRIKE-SSS-022:** Selected contracts MUST have distinct instrument IDs.
- **STRIKE-SSS-023:** Selected contracts MUST have the same underlying.
- **STRIKE-SSS-024:** Selected contracts MUST have the selected expiry.
- **STRIKE-SSS-025:** Selected contract deltas MUST retain their original signs.
- **STRIKE-SSS-026:** Recommendation legs are both `SELL` only as a structure hint.

---

## 7. Premium, POP, and risk metrics

All calculations use `Decimal` internally and are rounded only when sealing
public outputs. Monetary values are expressed in snapshot currency units per
underlying unit unless a multiplier is explicitly supplied.

### 7.1 Price policy

| Policy | Leg credit price | Use |
|---|---|---|
| `MID` | `(bid + ask) / 2` | Neutral estimate. |
| `ASK` | `ask` | Conservative entry-credit policy for a short sale. |

**METRIC-SSS-001:** A policy is applied to both legs consistently.

**METRIC-SSS-002:** If a required quote is unavailable, the strategy abstains.

**METRIC-SSS-003:** The policy estimate is not an executable fill prediction.

### 7.2 Credit and maximum profit

```text
call_credit = price(selected_call, premium_price_policy)
put_credit  = price(selected_put, premium_price_policy)
net_credit  = call_credit + put_credit
max_profit  = net_credit × contract_multiplier
```

For an unadjusted naked short strangle, maximum profit is the received net
credit when both options expire worthless. Fees, taxes, slippage, assignment,
and execution costs are excluded unless already represented by injected facts.

### 7.3 Probability-of-profit heuristic

The v1.0 POP is a transparent ranking heuristic:

```text
call_otm_probability = clamp(1 - abs(call_delta), 0, 1)
put_otm_probability  = clamp(1 - abs(put_delta), 0, 1)
joint_otm_heuristic  = max(0, call_otm_probability + put_otm_probability - 1)
credit_adjustment    = min(net_credit / max(spot, epsilon), 0.05)
pop                  = clamp(joint_otm_heuristic + credit_adjustment, 0, 1)
```

The formula intentionally avoids claiming independence between the two legs.
It is not a pricing model, distribution model, backtest, guarantee, or risk
limit. `epsilon` is an internal positive decimal used only after spot has
passed strict positivity validation.

### 7.4 Risk statement

| Metric | Required v1.0 value |
|---|---|
| `max_profit` | Estimated net credit multiplied by multiplier. |
| `max_loss` | `None` / `UNDEFINED_UNLIMITED`. |
| `risk_profile_hint` | `UNDEFINED_UNLIMITED`. |
| `capital_at_risk` | `None`; Risk Engine owns authoritative calculation. |
| `margin_required` | `None`; Risk Engine / broker owns authoritative calculation. |
| `breakevens` | Informational only when calculable from supplied multiplier. |

**METRIC-SSS-010:** The strategy MUST NOT label max loss as zero.

**METRIC-SSS-011:** The strategy MUST NOT substitute margin for max loss.

**METRIC-SSS-012:** The strategy MUST NOT invent a finite loss cap.

**METRIC-SSS-013:** Every entry explanation MUST include the undefined-risk warning.

---

## 8. Scoring integration

The strategy extracts facts and calls `StrategyScoringFramework.score()` with a
`FactorInputBundle`. The framework owns normalization, weighting, confidence
math, explanation sealing, and score serialization.

### 8.1 PREMIUM_SELLING factor map

| Factor category | Source | Strategy mapping |
|---|---|---|
| `MARKET_REGIME` | Injected regime tag and score | Range-bound/mean-reverting suitability. |
| `TREND_ALIGNMENT` | Injected trend evidence | Penalizes directional trend. |
| `VOLATILITY` | IV rank and IV evidence | Rewards elevated IV above floor. |
| `LIQUIDITY` | Selected-leg quote/OI/volume facts | Rewards tight, liquid selected legs. |
| `GREEKS` | Selected deltas | Rewards target proximity and balanced magnitudes. |
| `RISK_REWARD` | Credit, POP heuristic, undefined-risk label | Score is suitability only; never conceals undefined loss. |
| `EVENT_RISK` | Injected event flags | Penalizes known elevated event risk. |

- **SCORE-SSS-001:** Factor provenance MUST identify snapshot or injected metadata origin.
- **SCORE-SSS-002:** No factor may be fabricated to fill missing mandatory evidence.
- **SCORE-SSS-003:** The score profile defaults to `PREMIUM_SELLING`.
- **SCORE-SSS-004:** Unknown profile names reject configuration.
- **SCORE-SSS-005:** A sealed score does not authorize an entry.

### 8.2 Confidence mapping

The strategy forwards the framework-produced `ConfidenceReport` unchanged.
`SignalConfidence` is mapped from its band using the common project mapping.
An abstention may have high confidence: high confidence can mean strong
evidence that conditions are unsuitable.

---

## 9. TradingSignal mapping

| Recommendation state | TradingSignal action | Structure hint | Meaning |
|---|---|---|---|
| `ENTER` | `ENTER` | Two short legs | Suitable analytical candidate; downstream approval required. |
| `ABSTAIN` | `ABSTAIN` | None | Valid context, insufficient suitability now. |
| `REJECT` | `REJECT` | None | Invalid, stale, unsupported, or prohibited input. |

For `ENTER`, direction is `NEUTRAL`; the structure hint contains two legs:
one `SELL` CE and one `SELL` PE with exact selected contract identity. It is a
declarative recommendation, never an order request.

- **SIGNAL-SSS-001:** Signal reasons are stable and ordered by gate sequence.
- **SIGNAL-SSS-002:** `ENTER` includes score, confidence, explanation, and recommendation ID.
- **SIGNAL-SSS-003:** `ABSTAIN` includes all successful gate observations before the first failure.
- **SIGNAL-SSS-004:** `REJECT` includes a stable error code and safe details.
- **SIGNAL-SSS-005:** A signal does not expose credentials, portfolio account identifiers, or raw secrets.

---

## 10. Configuration

`ShortStrangleConfiguration` is a frozen dataclass. All values are validated at
construction; an invalid configuration cannot be used to evaluate a snapshot.

| Field | Type | Default | Validation |
|---|---|---|---|
| `target_delta` | `Decimal` | `0.16` | `(0, 0.50)` |
| `call_target_delta` | `Decimal | None` | `None` | Uses shared target when absent. |
| `put_target_delta` | `Decimal | None` | `None` | Uses shared target when absent. |
| `minimum_iv_rank` | `Decimal` | `50` | `[0, 100]` |
| `maximum_spread_width` | `Decimal | None` | `None` | Positive when set. |
| `maximum_relative_spread_width` | `Decimal` | `0.15` | `(0, 1]` |
| `minimum_premium` | `Decimal` | `0` | Non-negative. |
| `minimum_open_interest` | `int` | `1` | Non-negative. |
| `minimum_volume` | `int` | `1` | Non-negative. |
| `entry_time_window` | `TimeWindow` | exchange config | Valid same-day interval. |
| `exit_time_window` | `TimeWindow` | exchange config | Informational only. |
| `scoring_profile_name` | `str` | `PREMIUM_SELLING` | Known profile. |
| `supported_underlyings` | `frozenset[str]` | NIFTY/BANKNIFTY/SENSEX | Non-empty normalized values. |
| `max_snapshot_age_seconds` | `int` | `5` | Positive. |
| `require_valid_snapshot` | `bool` | `True` | Boolean. |
| `delta_selection_tolerance` | `Decimal` | `0.03` | `[0, 0.50)`. |
| `premium_price_policy` | `PremiumPricePolicy` | `MID` | Known enum. |
| `minimum_dte` | `int` | `0` | Non-negative. |
| `maximum_dte` | `int` | `45` | At least minimum DTE. |
| `require_iv_rank` | `bool` | `True` | Boolean. |
| `require_greeks` | `bool` | `True` | Boolean. |
| `require_open_interest` | `bool` | `True` | Boolean. |
| `require_volume` | `bool` | `True` | Boolean. |
| `iv_rank_lookback_observations` | `int` | `252` | Positive. |

### 10.1 Configuration invariants

- **CFG-SSS-001:** Decimal fields must be finite.
- **CFG-SSS-002:** A call target overrides only the call target.
- **CFG-SSS-003:** A put target overrides only the put target.
- **CFG-SSS-004:** Target magnitudes must be strictly less than 0.50.
- **CFG-SSS-005:** `minimum_premium` is evaluated on total net credit.
- **CFG-SSS-006:** An empty underlying set is invalid.
- **CFG-SSS-007:** Underlying strings are normalized to uppercase at construction.
- **CFG-SSS-008:** `maximum_dte < minimum_dte` is invalid.
- **CFG-SSS-009:** A `None` absolute spread limit disables only that limit.
- **CFG-SSS-010:** Exit-window configuration never becomes exit behavior.

---

## 11. Frozen public models

### 11.1 `ShortStrangleStrikeSelection`

| Field | Type | Meaning |
|---|---|---|
| `underlying` | `str` | Normalized underlying identity. |
| `spot` | `Decimal` | Evaluation spot. |
| `expiry` | `date` | Shared selected expiry. |
| `call_strike` | `Decimal` | Selected OTM CE strike. |
| `put_strike` | `Decimal` | Selected OTM PE strike. |
| `call_symbol` | `str | None` | Supplied contract symbol. |
| `put_symbol` | `str | None` | Supplied contract symbol. |
| `call_token` | `int | str | None` | Supplied token; never fetched. |
| `put_token` | `int | str | None` | Supplied token; never fetched. |
| `call_delta` | `Decimal` | Original signed CE delta. |
| `put_delta` | `Decimal` | Original signed PE delta. |
| `call_delta_error` | `Decimal` | Target distance. |
| `put_delta_error` | `Decimal` | Target distance. |

### 11.2 `ShortStrangleRiskMetrics`

| Field | Type | Meaning |
|---|---|---|
| `call_credit` | `Decimal` | Policy-estimated call credit. |
| `put_credit` | `Decimal` | Policy-estimated put credit. |
| `net_credit` | `Decimal` | Sum of policy credits. |
| `max_profit` | `Decimal` | Estimated credit times multiplier. |
| `max_loss` | `None` | Always `None` for naked strategy. |
| `max_loss_label` | `str` | Exactly `UNDEFINED_UNLIMITED`. |
| `probability_of_profit` | `Decimal` | Heuristic in `[0, 1]`. |
| `call_otm_probability` | `Decimal` | Delta-derived heuristic. |
| `put_otm_probability` | `Decimal` | Delta-derived heuristic. |
| `contract_multiplier` | `Decimal` | Supplied multiplier. |
| `risk_warning` | `str` | Mandatory naked-risk warning. |

### 11.3 `ShortStrangleRecommendation`

| Field | Type | Meaning |
|---|---|---|
| `recommendation_id` | `str` | Deterministic context-derived identifier. |
| `state` | `EntryRecommendationState` | `ENTER`, `ABSTAIN`, or `REJECT`. |
| `strategy_id` | `str` | Always `short_strangle`. |
| `observed_at` | `datetime` | Injected snapshot observation time. |
| `selection` | `ShortStrangleStrikeSelection | None` | Present only for entry. |
| `risk_metrics` | `ShortStrangleRiskMetrics | None` | Present only for entry. |
| `score` | `StrategyScore | None` | Sealed framework output. |
| `confidence` | `ConfidenceReport | None` | Sealed framework output. |
| `explanation` | `StrategyExplanation` | Ordered reasons. |
| `exit_window_hint` | `TimeWindow` | Informational metadata only. |
| `reasons` | `tuple[str, ...]` | Stable reason codes. |

### 11.4 `ShortStrangleEvaluationResult`

This private-to-plugin sealed artifact contains the validated context key, gate
ledger, optional recommendation, factor bundle, and mapped `TradingSignal`.
It prevents `run()` from recomputing disparate facts when it maps evaluation
evidence to a signal.

---

## 12. Public API

```python
class ShortStrangleStrategy(BaseStrategy):
    """Evaluate injected evidence for a naked short-strangle candidate."""

    def __init__(
        self,
        configuration: ShortStrangleConfiguration,
        scoring_framework: StrategyScoringFramework,
        event_sink: StrategyEventSink | None = None,
    ) -> None:
        """Create a stateless strategy with immutable collaborators."""

    def run(self, context: StrategyContext) -> TradingSignal:
        """Return a deterministic signal without external side effects."""

    def evaluate(
        self,
        context: StrategyContext,
    ) -> ShortStrangleRecommendation:
        """Return the complete recommendation artifact for injected context."""
```

`run()` calls the same internal evaluation path as `evaluate()` and maps the
sealed result once. It does not call any broker, Risk Engine, Trade Decision
Engine, or Order Manager.

### 12.1 StrategyContext extensions

| Metadata key | Type | Required | Meaning |
|---|---|---|---|
| `historical_series` | `HistoricalData | HistoricalSeries` | No | Already-injected historical IV/RV context. |
| `portfolio_snapshot` | `PortfolioSnapshot` | No | Informational, read-only context. |
| `risk_profile` | `RiskProfile` | No | Informational preference; never enforced. |
| `regime_evidence` | `MarketRegimeEvidence` | Yes | Regime tag and observation facts. |
| `event_risk_evidence` | `EventRiskEvidence` | Yes | Supplied event flags; never fetched. |
| `clock` | `Clock` | No | Test/audit collaborator where project contract permits. |

Typed optional fields are preferred if `StrategyContext` already supports them.
Metadata keys exist only as backward-compatible extension points and do not
authorize I/O.

---

## 13. Validation

Validation is performed before scoring and before the plugin can emit `ENTER`.

| ID | Condition | Outcome | Code |
|---|---|---|---|
| VAL-SSS-001 | Context is absent | REJECT | `SSS.CONTEXT.MISSING` |
| VAL-SSS-002 | Snapshot is absent | REJECT | `SSS.SNAPSHOT.MISSING` |
| VAL-SSS-003 | Snapshot is invalid | REJECT | `SSS.SNAPSHOT.INVALID` |
| VAL-SSS-004 | Snapshot is stale | REJECT | `SSS.SNAPSHOT.STALE` |
| VAL-SSS-005 | Underlying unsupported | REJECT | `SSS.UNDERLYING.UNSUPPORTED` |
| VAL-SSS-006 | Spot invalid | REJECT | `SSS.SPOT.INVALID` |
| VAL-SSS-007 | Outside entry window | ABSTAIN | `SSS.TIME.OUTSIDE_ENTRY_WINDOW` |
| VAL-SSS-008 | Regime absent | REJECT | `SSS.REGIME.MISSING` |
| VAL-SSS-009 | Regime unsuitable | ABSTAIN | `SSS.REGIME.UNSUITABLE` |
| VAL-SSS-010 | Crisis regime | REJECT | `SSS.REGIME.CRISIS` |
| VAL-SSS-011 | IV rank absent | REJECT | `SSS.IV_RANK.MISSING` |
| VAL-SSS-012 | IV rank below floor | ABSTAIN | `SSS.IV_RANK.LOW` |
| VAL-SSS-013 | Chain absent | REJECT | `SSS.CHAIN.MISSING` |
| VAL-SSS-014 | Chain incomplete | REJECT | `SSS.CHAIN.INCOMPLETE` |
| VAL-SSS-015 | No liquid CE | ABSTAIN | `SSS.LIQUIDITY.NO_CALL` |
| VAL-SSS-016 | No liquid PE | ABSTAIN | `SSS.LIQUIDITY.NO_PUT` |
| VAL-SSS-017 | Greek absent | REJECT | `SSS.GREEKS.MISSING` |
| VAL-SSS-018 | No delta candidate | ABSTAIN | `SSS.STRIKE.NO_ELIGIBLE_CANDIDATE` |
| VAL-SSS-019 | Premium below floor | ABSTAIN | `SSS.PREMIUM.BELOW_MINIMUM` |
| VAL-SSS-020 | Metric non-finite | REJECT | `SSS.METRIC.NON_FINITE` |

---

## 14. Determinism and thread safety

The strategy is stateless. It retains immutable configuration and immutable
collaborator references only. Every local collection is constructed per
invocation. It uses no random source, no mutable singleton, no cache, no
network, and no ambient clock.

- **DET-SSS-001:** Candidate collections are sorted with explicit total keys.
- **DET-SSS-002:** Decimal arithmetic uses a fixed documented context.
- **DET-SSS-003:** Reason ordering follows gate order, not hash iteration.
- **DET-SSS-004:** Canonical JSON sorts keys and uses stable enum values.
- **DET-SSS-005:** Concurrent `run()` calls cannot observe partial state.
- **DET-SSS-006:** Optional event-sink failures cannot alter a sealed result.
- **DET-SSS-007:** The strategy emits no mutable collection in public output.

### 14.1 Concurrency sketch

```text
Thread A: context A → local validation → local selection → sealed result A
Thread B: context B → local validation → local selection → sealed result B
                       ↑ shared immutable configuration/framework reference ↑
```

The scoring framework remains responsible for synchronization if its own
implementation maintains optional statistics or cache state.

---

## 15. Serialization

Public recommendation models support canonical versioned JSON.

| Rule | Requirement |
|---|---|
| SER-SSS-001 | Payload contains `schema_version: "1.0"`. |
| SER-SSS-002 | Enums serialize to stable string values. |
| SER-SSS-003 | Decimal values serialize as strings. |
| SER-SSS-004 | Datetimes serialize as UTC ISO-8601 with offset. |
| SER-SSS-005 | `None` max loss is serialized as JSON `null`. |
| SER-SSS-006 | `max_loss_label` remains `UNDEFINED_UNLIMITED`. |
| SER-SSS-007 | Unknown fields may be retained only by explicit reader policy. |
| SER-SSS-008 | Unknown required enum values reject deserialization. |
| SER-SSS-009 | Deserialization revalidates all configuration-independent invariants. |
| SER-SSS-010 | Canonical serialization has sorted keys and no secret fields. |

---

## 16. Error catalog

| Code | Classification | Safe message |
|---|---|---|
| `SSS.CONFIG.INVALID` | Reject | Short-strangle configuration is invalid. |
| `SSS.CONTEXT.MISSING` | Reject | Strategy context is required. |
| `SSS.SNAPSHOT.MISSING` | Reject | Market snapshot is required. |
| `SSS.SNAPSHOT.INVALID` | Reject | Market snapshot failed validation. |
| `SSS.SNAPSHOT.STALE` | Reject | Market snapshot exceeded maximum age. |
| `SSS.UNDERLYING.UNSUPPORTED` | Reject | Underlying is not supported by configuration. |
| `SSS.SPOT.INVALID` | Reject | Underlying spot is invalid. |
| `SSS.TIME.OUTSIDE_ENTRY_WINDOW` | Abstain | Observation is outside the entry window. |
| `SSS.REGIME.MISSING` | Reject | Regime evidence is required. |
| `SSS.REGIME.UNSUITABLE` | Abstain | Regime is unsuitable for a short strangle. |
| `SSS.REGIME.CRISIS` | Reject | Crisis regime prohibits a short strangle. |
| `SSS.IV_RANK.MISSING` | Reject | IV rank evidence is required. |
| `SSS.IV_RANK.LOW` | Abstain | IV rank is below the configured minimum. |
| `SSS.CHAIN.MISSING` | Reject | Option chain is required. |
| `SSS.CHAIN.INCOMPLETE` | Reject | Option chain lacks compatible call and put evidence. |
| `SSS.GREEKS.MISSING` | Reject | Required option Greek is missing. |
| `SSS.LIQUIDITY.NO_CALL` | Abstain | No liquid call candidate remains. |
| `SSS.LIQUIDITY.NO_PUT` | Abstain | No liquid put candidate remains. |
| `SSS.STRIKE.NO_ELIGIBLE_CANDIDATE` | Abstain | No strike meets target-delta policy. |
| `SSS.PREMIUM.BELOW_MINIMUM` | Abstain | Estimated credit is below configured minimum. |
| `SSS.METRIC.NON_FINITE` | Reject | A derived metric is not finite. |
| `SSS.SCORING.FAILED` | Reject | Scoring framework rejected supplied factors. |
| `SSS.SERIALIZATION.INVALID` | Reject | Recommendation payload is invalid. |

---

## 17. Security

- **SEC-SSS-001:** This module receives no credentials and stores none.
- **SEC-SSS-002:** This module reads no `.env` file.
- **SEC-SSS-003:** Logs must not include account identifiers or raw portfolio holdings.
- **SEC-SSS-004:** Error details must exclude secrets and credential-like metadata.
- **SEC-SSS-005:** Deserialization must bound collection sizes before allocation.
- **SEC-SSS-006:** Numeric payloads must reject NaN and infinity.
- **SEC-SSS-007:** Untrusted metadata must be type-checked before use.
- **SEC-SSS-008:** Event publication is observational and receives sanitized payloads.

---

## 18. Lifecycle and integration

1. Platform composition constructs immutable `ShortStrangleConfiguration`.
2. Composition injects `StrategyScoringFramework`.
3. Registry registers `ShortStrangleStrategy` under `short_strangle`.
4. Evaluation Engine builds a read-only `StrategyContext`.
5. Evaluation Engine calls `BaseStrategy.run(context)`.
6. The strategy emits a `TradingSignal` and recommendation evidence.
7. Evaluation Engine aggregates reports.
8. Trade Decision Engine selects or declines a candidate.
9. Risk independently validates an approved proposal.
10. Execution and Order Manager own all order actions.

### 18.1 Optional event topics

| Topic | When | Payload rule |
|---|---|---|
| `strategy.short_strangle.evaluated` | Every completed evaluation | Summary only, no secrets. |
| `strategy.short_strangle.abstained` | Valid but unsuitable result | Reason code and audit key. |
| `strategy.short_strangle.rejected` | Invalid/prohibited input | Safe error code and audit key. |
| `strategy.short_strangle.recommended` | Entry recommendation | Selection, score, undefined-risk label. |

Events do not change strategy behavior and a sink failure is isolated.

---

## 19. Testing

`tests/test_short_strangle_strategy.py` MUST achieve at least 95% line and
branch coverage for the production module.

| Test group | Minimum cases |
|---|---|
| Identity and registry | 5 |
| Configuration validation | 20 |
| Snapshot validation | 15 |
| Time window | 10 |
| Regime gate | 12 |
| IV-rank gate | 12 |
| Chain validation | 16 |
| Liquidity | 20 |
| Expiry selection | 12 |
| Strike ranking | 20 |
| Metric calculation | 15 |
| Scoring mapping | 10 |
| Signal mapping | 12 |
| Serialization | 15 |
| Determinism/concurrency | 10 |
| Boundary guarantees | 12 |

No test may call a network, broker, environment loader, or real wall clock.

---

## 20. Implementation checklist

- [ ] Create immutable enums and frozen dataclasses.
- [ ] Implement configuration validation.
- [ ] Implement context adapter without I/O.
- [ ] Implement ordered gate ledger.
- [ ] Implement deterministic expiry grouping.
- [ ] Implement deterministic eligible-candidate filtering.
- [ ] Implement explicit candidate ranking key.
- [ ] Implement price-policy credit calculation.
- [ ] Implement POP heuristic and undefined-risk metrics.
- [ ] Build factor bundle and call scoring framework.
- [ ] Map result to signal and recommendation.
- [ ] Add canonical serialization.
- [ ] Register `short_strangle`.
- [ ] Add unit tests and coverage gate.
- [ ] Verify no prohibited imports.

## 21. Definition of Done

The module is done when all gates, selection rules, calculations, serialization
rules, registry integration, and tests in this specification are implemented;
coverage is at least 95%; no prohibited boundary is crossed; and every entry
recommendation clearly states that naked short-strangle max loss is undefined
and unlimited.

## 22. Non-goals

v1.0 does not implement orders, fills, position adjustment, hedging, exit
management, portfolio risk, margin, sizing, broker retrieval, model training,
or profitability guarantees.

---

## Appendix A — Worked NIFTY evaluation

| Fact | Value |
|---|---|
| Underlying | `NIFTY` |
| Spot | `22,400` |
| Regime | `RANGE_BOUND` |
| IV rank | `63` |
| Target delta | `0.16` |
| Selected CE | `22,900 CE`, delta `0.15` |
| Selected PE | `21,900 PE`, delta `-0.16` |
| Price policy | `MID` |
| Call credit | `84.00` |
| Put credit | `79.00` |
| Net credit | `163.00` |
| Multiplier | `25` |
| Estimated max profit | `4,075.00` |
| Max loss | `UNDEFINED_UNLIMITED` |

The strategy may emit `ENTER` only if every other gate passes. A downstream
Risk Engine may still reject the proposal.

## Appendix B — Candidate selection examples

| Side | Strike | Delta | Delta error | Relative spread | OI | Rank outcome |
|---|---:|---:|---:|---:|---:|---|
| CE | 22,800 | 0.18 | 0.02 | 0.11 | 125,000 | Second |
| CE | 22,900 | 0.15 | 0.01 | 0.12 | 110,000 | First |
| CE | 23,000 | 0.13 | 0.03 | 0.06 | 140,000 | Third |
| PE | 22,000 | -0.19 | 0.03 | 0.08 | 160,000 | Third |
| PE | 21,900 | -0.16 | 0.00 | 0.13 | 100,000 | First |
| PE | 21,800 | -0.14 | 0.02 | 0.08 | 170,000 | Second |

## Appendix C — IV rank examples

| IV rank | Minimum | Outcome | Code |
|---:|---:|---|---|
| 72 | 50 | PASS | none |
| 50 | 50 | PASS | none |
| 49.9999 | 50 | ABSTAIN | `SSS.IV_RANK.LOW` |
| missing | 50 | REJECT | `SSS.IV_RANK.MISSING` |
| NaN | 50 | REJECT | `SSS.IV_RANK.MISSING` |

## Appendix D — Liquidity rejects

| Observation | Outcome | Reason |
|---|---|---|
| Bid absent | Reject candidate | Cannot calculate a valid midpoint. |
| Ask below bid | Reject candidate | Quote is crossed. |
| Relative spread 0.16 with max 0.15 | Abstain candidate | Spread exceeds threshold. |
| OI 99 with floor 100 | Abstain candidate | OI floor not met. |
| Volume absent and required | Reject candidate | Required liquidity fact is missing. |

## Appendix E — Factor bundle example

```text
MARKET_REGIME: RANGE_BOUND, provenance=snapshot.regime
TREND_ALIGNMENT: 25.0, provenance=snapshot.trend
VOLATILITY: 63.0, provenance=snapshot.iv_rank
LIQUIDITY: 88.0, provenance=selected_contract_quotes
GREEKS: 92.0, provenance=selected_contract_deltas
RISK_REWARD: 55.0, provenance=credit_and_pop_heuristic
EVENT_RISK: 80.0, provenance=context.event_risk_evidence
```

## Appendix F — TradingSignal example

```json
{
  "action": "ENTER",
  "strategy_id": "short_strangle",
  "direction": "NEUTRAL",
  "structure_hint": {
    "legs": [
      {"side": "SELL", "option_type": "CE", "strike": "22900"},
      {"side": "SELL", "option_type": "PE", "strike": "21900"}
    ]
  },
  "risk_profile_hint": "UNDEFINED_UNLIMITED"
}
```

## Appendix G — Failure matrix

| Stage | Invalid input | Response | Downstream effect |
|---|---|---|---|
| Freshness | Snapshot too old | REJECT | No entry structure. |
| Regime | Trend breakout | ABSTAIN | Evaluation report remains comparable. |
| Volatility | IV rank too low | ABSTAIN | No selected legs. |
| Liquidity | Wide call quote | ABSTAIN | No entry structure. |
| Greeks | Delta absent | REJECT | No inferred delta. |
| Premium | Credit below floor | ABSTAIN | No entry structure. |

## Appendix H — Concurrency acceptance cases

| Case | Required result |
|---|---|
| Same context on 100 threads | Byte-equivalent canonical recommendation payload. |
| Distinct contexts on 100 threads | No shared mutable state or cross-contamination. |
| Event sink throws | Result remains sealed; sink error is isolated. |
| Framework cache enabled | Framework owns any internal lock; strategy remains stateless. |

## Appendix I — Glossary

| Term | Meaning |
|---|---|
| CE | Call option contract. |
| PE | Put option contract. |
| OTM | Out of the money at evaluation spot. |
| IV rank | Relative position of current IV in an injected historical range. |
| POP | Probability-of-profit heuristic, not a guarantee. |
| Net credit | Sum of estimated premiums received for both short legs. |
| Naked risk | A loss profile without a strategy-provided finite cap. |

## Appendix J — Legacy migration

| Legacy behavior | v1.0 replacement |
|---|---|
| Strategy directly fetches quotes | Inject `MarketSnapshot` through context. |
| Strategy places an order | Emit `TradingSignal` only. |
| Mutable settings dictionary | Frozen `ShortStrangleConfiguration`. |
| Implicit strike order | Explicit deterministic ranking key. |
| Finite guessed loss | `UNDEFINED_UNLIMITED` risk label. |

## Appendix K — Benchmark contract

| Benchmark | Target |
|---|---|
| Typical 200-contract snapshot evaluation | p95 below 10 ms on reference developer hardware. |
| 1,000-contract snapshot evaluation | p95 below 30 ms on reference developer hardware. |
| External calls | Zero. |
| Allocation growth | Linear in option-chain count. |
| Candidate sorting | `O(n log n)` per selected expiry. |

## Appendix L — Default profile rationale

`PREMIUM_SELLING` emphasizes volatility realization opportunity, liquidity,
Greek suitability, and adverse-event awareness. It does not treat a high score
as permission to accept unlimited risk.

## Appendix M — Delta interpretation

| Absolute delta | Interpretation |
|---:|---|
| 0.10 | Farther OTM; lower credit, lower delta heuristic. |
| 0.16 | Default short-strangle target. |
| 0.20 | Closer OTM; higher credit and closer strike. |
| 0.30 | Usually outside default tolerance for target 0.16. |

## Appendix N — POP notes

The POP heuristic is deliberately conservative in claims, not necessarily in
numeric value. It ranks injected contract facts; it cannot model jumps,
overnight gaps, early assignment, volatility skew movement, execution, or
correlation. Consumers must display it as an estimate.

## Appendix O — Structured reason catalog

| Reason code | Human explanation |
|---|---|
| `REGIME_RANGE_BOUND` | Regime supports neutral premium selling. |
| `IV_RANK_SUFFICIENT` | IV rank exceeds configured floor. |
| `LIQUIDITY_VALIDATED` | Both selected legs passed quote and depth rules. |
| `NAKED_RISK_UNDEFINED` | Loss is undefined/unlimited for this strategy. |
| `ENTRY_REQUIRES_DOWNSTREAM_APPROVAL` | Signal is not an order or approval. |

## Appendix P — Audit fields

| Field | Reason |
|---|---|
| `recommendation_id` | Correlates evaluation artifacts. |
| `snapshot_id` | Links evidence to injected snapshot. |
| `observed_at` | Records observation time. |
| `configuration_fingerprint` | Identifies immutable policy. |
| `factor_bundle_hash` | Identifies scoring input. |
| `schema_version` | Enables safe evolution. |

## Appendix Q — Observability boundaries

Metrics may count evaluations, entries, abstentions, rejects, and gate causes.
Metrics must never calculate trading P&L, query positions, submit orders, or
leak contract/account secrets.

## Appendix R — Numerical rules

1. Convert accepted numeric market facts to `Decimal` at the boundary.
2. Reject NaN, infinity, and signed zero where strict positivity is required.
3. Preserve supplied delta sign.
4. Quantize only at documented presentation boundaries.
5. Do not compare raw binary floats as ranking keys.

## Appendix S — Time rules

| Rule | Requirement |
|---|---|
| Snapshot age | Computed from injected evaluation time and observed time. |
| Entry window | Uses exchange-local time. |
| Expiry | Normalized to exchange date before DTE calculation. |
| Audit time | Serialized in UTC. |
| Wall clock | Never read implicitly by strategy logic. |

## Appendix T — Data provenance

Every key calculation records a provenance label. Permitted labels include
`snapshot.option_chain`, `snapshot.iv_rank`, `context.regime_evidence`,
`context.historical_series`, and `context.event_risk_evidence`. A provenance
label is evidence tracing, not an I/O instruction.

## Appendix U — Compatibility notes

The strategy depends on stable project interfaces rather than concrete broker
types. If `StrategyContext` adds typed optional fields in a later release, the
metadata adapter remains a compatibility layer until the migration window ends.

## Appendix V — Rejection precedence

| Higher precedence | Lower precedence |
|---|---|
| Missing/invalid context | Unsuitable market condition |
| Stale snapshot | Low IV rank |
| Crisis regime | Candidate liquidity |
| Invalid required Greek | Premium floor |
| Scoring validation failure | Optional observability failure |

## Appendix W — Entry payload constraints

An `ENTER` payload must include one expiry, two distinct instruments, one CE,
one PE, both SELL structure legs, net credit, POP heuristic, score,
confidence, explanation, and `UNDEFINED_UNLIMITED`. It must not include an
order ID, quantity, broker account, margin approval, or fill status.

## Appendix X — Exit metadata constraints

`exit_time_window` is a descriptive recommendation attribute only. It must
not cause an order, alert that submits an order, state mutation, scheduling
loop, or position-management decision.

## Appendix Y — Implementation hazards

| Hazard | Required prevention |
|---|---|
| Call/put expiry mismatch | Select expiry before legs. |
| Unstable chain order | Apply total sort key. |
| Floating tie drift | Normalize Decimal values. |
| Assumed finite naked risk | Emit undefined risk label. |
| Broker convenience call | Prohibit imports and test for absence. |
| Snapshot mutation | Use only immutable models and local tuples. |

## Appendix Z — Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-08-05 | Initial locked-contract specification. |


---
## Appendices AA–BD — Normative verification and operations catalog
These appendices define compact, independently executable acceptance vectors. Each vector is a required unit, integration, or property test scenario; it is not an instruction to perform I/O.

## Appendix AA — Validation vector catalog
The vectors below verify configuration and context validation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AA-V01 | nominal valid input for configuration and context validation | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V02 | required field absent for configuration and context validation | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V03 | non-finite numeric value for configuration and context validation | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V04 | boundary value equal to minimum for configuration and context validation | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V05 | value one quantum below minimum for configuration and context validation | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V06 | value one quantum above maximum for configuration and context validation | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V07 | timestamp exactly at start boundary for configuration and context validation | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V08 | timestamp exactly at end boundary for configuration and context validation | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V09 | two otherwise equal candidates for configuration and context validation | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V10 | input collection reversed for configuration and context validation | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V11 | unrelated metadata added for configuration and context validation | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V12 | optional metadata absent for configuration and context validation | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V13 | optional metadata malformed for configuration and context validation | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V14 | immutable input reused for configuration and context validation | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V15 | same input evaluated twice for configuration and context validation | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V16 | same input evaluated concurrently for configuration and context validation | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V17 | event sink raises exception for configuration and context validation | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V18 | unknown enum supplied for configuration and context validation | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V19 | unknown payload field supplied for configuration and context validation | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V20 | unsupported underlying supplied for configuration and context validation | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V21 | snapshot id conflicts with contract identity for configuration and context validation | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V22 | selected contract token absent for configuration and context validation | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V23 | symbol absent but immutable instrument id present for configuration and context validation | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V24 | decimal precision exceeds display precision for configuration and context validation | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V25 | negative zero submitted for configuration and context validation | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V26 | large but finite option chain for configuration and context validation | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V27 | duplicate contract id with same facts for configuration and context validation | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V28 | duplicate contract id with conflicting facts for configuration and context validation | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V29 | score framework rejects factor bundle for configuration and context validation | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V30 | score framework returns sealed score for configuration and context validation | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V31 | RiskProfile prefers no entry for configuration and context validation | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V32 | PortfolioSnapshot contains exposure for configuration and context validation | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V33 | HistoricalSeries is injected for configuration and context validation | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V34 | broker adapter is available in process for configuration and context validation | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V35 | environment contains credentials for configuration and context validation | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V36 | order manager exists in composition for configuration and context validation | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V37 | risk engine exists in composition for configuration and context validation | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V38 | trade decision exists downstream for configuration and context validation | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V39 | serialization round trip for configuration and context validation | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AA-V40 | schema version incompatible for configuration and context validation | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AA-ACCEPT-001:** All forty vectors pass without external calls.
**AA-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AA-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AB — Snapshot freshness catalog
The vectors below verify snapshot identity and freshness. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AB-V01 | nominal valid input for snapshot identity and freshness | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V02 | required field absent for snapshot identity and freshness | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V03 | non-finite numeric value for snapshot identity and freshness | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V04 | boundary value equal to minimum for snapshot identity and freshness | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V05 | value one quantum below minimum for snapshot identity and freshness | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V06 | value one quantum above maximum for snapshot identity and freshness | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V07 | timestamp exactly at start boundary for snapshot identity and freshness | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V08 | timestamp exactly at end boundary for snapshot identity and freshness | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V09 | two otherwise equal candidates for snapshot identity and freshness | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V10 | input collection reversed for snapshot identity and freshness | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V11 | unrelated metadata added for snapshot identity and freshness | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V12 | optional metadata absent for snapshot identity and freshness | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V13 | optional metadata malformed for snapshot identity and freshness | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V14 | immutable input reused for snapshot identity and freshness | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V15 | same input evaluated twice for snapshot identity and freshness | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V16 | same input evaluated concurrently for snapshot identity and freshness | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V17 | event sink raises exception for snapshot identity and freshness | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V18 | unknown enum supplied for snapshot identity and freshness | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V19 | unknown payload field supplied for snapshot identity and freshness | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V20 | unsupported underlying supplied for snapshot identity and freshness | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V21 | snapshot id conflicts with contract identity for snapshot identity and freshness | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V22 | selected contract token absent for snapshot identity and freshness | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V23 | symbol absent but immutable instrument id present for snapshot identity and freshness | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V24 | decimal precision exceeds display precision for snapshot identity and freshness | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V25 | negative zero submitted for snapshot identity and freshness | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V26 | large but finite option chain for snapshot identity and freshness | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V27 | duplicate contract id with same facts for snapshot identity and freshness | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V28 | duplicate contract id with conflicting facts for snapshot identity and freshness | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V29 | score framework rejects factor bundle for snapshot identity and freshness | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V30 | score framework returns sealed score for snapshot identity and freshness | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V31 | RiskProfile prefers no entry for snapshot identity and freshness | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V32 | PortfolioSnapshot contains exposure for snapshot identity and freshness | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V33 | HistoricalSeries is injected for snapshot identity and freshness | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V34 | broker adapter is available in process for snapshot identity and freshness | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V35 | environment contains credentials for snapshot identity and freshness | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V36 | order manager exists in composition for snapshot identity and freshness | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V37 | risk engine exists in composition for snapshot identity and freshness | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V38 | trade decision exists downstream for snapshot identity and freshness | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V39 | serialization round trip for snapshot identity and freshness | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AB-V40 | schema version incompatible for snapshot identity and freshness | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AB-ACCEPT-001:** All forty vectors pass without external calls.
**AB-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AB-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AC — Regime gate catalog
The vectors below verify regime and trend suitability. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AC-V01 | nominal valid input for regime and trend suitability | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V02 | required field absent for regime and trend suitability | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V03 | non-finite numeric value for regime and trend suitability | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V04 | boundary value equal to minimum for regime and trend suitability | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V05 | value one quantum below minimum for regime and trend suitability | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V06 | value one quantum above maximum for regime and trend suitability | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V07 | timestamp exactly at start boundary for regime and trend suitability | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V08 | timestamp exactly at end boundary for regime and trend suitability | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V09 | two otherwise equal candidates for regime and trend suitability | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V10 | input collection reversed for regime and trend suitability | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V11 | unrelated metadata added for regime and trend suitability | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V12 | optional metadata absent for regime and trend suitability | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V13 | optional metadata malformed for regime and trend suitability | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V14 | immutable input reused for regime and trend suitability | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V15 | same input evaluated twice for regime and trend suitability | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V16 | same input evaluated concurrently for regime and trend suitability | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V17 | event sink raises exception for regime and trend suitability | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V18 | unknown enum supplied for regime and trend suitability | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V19 | unknown payload field supplied for regime and trend suitability | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V20 | unsupported underlying supplied for regime and trend suitability | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V21 | snapshot id conflicts with contract identity for regime and trend suitability | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V22 | selected contract token absent for regime and trend suitability | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V23 | symbol absent but immutable instrument id present for regime and trend suitability | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V24 | decimal precision exceeds display precision for regime and trend suitability | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V25 | negative zero submitted for regime and trend suitability | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V26 | large but finite option chain for regime and trend suitability | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V27 | duplicate contract id with same facts for regime and trend suitability | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V28 | duplicate contract id with conflicting facts for regime and trend suitability | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V29 | score framework rejects factor bundle for regime and trend suitability | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V30 | score framework returns sealed score for regime and trend suitability | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V31 | RiskProfile prefers no entry for regime and trend suitability | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V32 | PortfolioSnapshot contains exposure for regime and trend suitability | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V33 | HistoricalSeries is injected for regime and trend suitability | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V34 | broker adapter is available in process for regime and trend suitability | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V35 | environment contains credentials for regime and trend suitability | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V36 | order manager exists in composition for regime and trend suitability | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V37 | risk engine exists in composition for regime and trend suitability | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V38 | trade decision exists downstream for regime and trend suitability | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V39 | serialization round trip for regime and trend suitability | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AC-V40 | schema version incompatible for regime and trend suitability | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AC-ACCEPT-001:** All forty vectors pass without external calls.
**AC-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AC-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AD — Volatility gate catalog
The vectors below verify IV and IV-rank suitability. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AD-V01 | nominal valid input for IV and IV-rank suitability | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V02 | required field absent for IV and IV-rank suitability | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V03 | non-finite numeric value for IV and IV-rank suitability | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V04 | boundary value equal to minimum for IV and IV-rank suitability | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V05 | value one quantum below minimum for IV and IV-rank suitability | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V06 | value one quantum above maximum for IV and IV-rank suitability | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V07 | timestamp exactly at start boundary for IV and IV-rank suitability | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V08 | timestamp exactly at end boundary for IV and IV-rank suitability | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V09 | two otherwise equal candidates for IV and IV-rank suitability | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V10 | input collection reversed for IV and IV-rank suitability | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V11 | unrelated metadata added for IV and IV-rank suitability | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V12 | optional metadata absent for IV and IV-rank suitability | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V13 | optional metadata malformed for IV and IV-rank suitability | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V14 | immutable input reused for IV and IV-rank suitability | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V15 | same input evaluated twice for IV and IV-rank suitability | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V16 | same input evaluated concurrently for IV and IV-rank suitability | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V17 | event sink raises exception for IV and IV-rank suitability | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V18 | unknown enum supplied for IV and IV-rank suitability | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V19 | unknown payload field supplied for IV and IV-rank suitability | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V20 | unsupported underlying supplied for IV and IV-rank suitability | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V21 | snapshot id conflicts with contract identity for IV and IV-rank suitability | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V22 | selected contract token absent for IV and IV-rank suitability | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V23 | symbol absent but immutable instrument id present for IV and IV-rank suitability | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V24 | decimal precision exceeds display precision for IV and IV-rank suitability | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V25 | negative zero submitted for IV and IV-rank suitability | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V26 | large but finite option chain for IV and IV-rank suitability | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V27 | duplicate contract id with same facts for IV and IV-rank suitability | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V28 | duplicate contract id with conflicting facts for IV and IV-rank suitability | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V29 | score framework rejects factor bundle for IV and IV-rank suitability | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V30 | score framework returns sealed score for IV and IV-rank suitability | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V31 | RiskProfile prefers no entry for IV and IV-rank suitability | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V32 | PortfolioSnapshot contains exposure for IV and IV-rank suitability | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V33 | HistoricalSeries is injected for IV and IV-rank suitability | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V34 | broker adapter is available in process for IV and IV-rank suitability | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V35 | environment contains credentials for IV and IV-rank suitability | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V36 | order manager exists in composition for IV and IV-rank suitability | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V37 | risk engine exists in composition for IV and IV-rank suitability | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V38 | trade decision exists downstream for IV and IV-rank suitability | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V39 | serialization round trip for IV and IV-rank suitability | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AD-V40 | schema version incompatible for IV and IV-rank suitability | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AD-ACCEPT-001:** All forty vectors pass without external calls.
**AD-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AD-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AE — Time-window catalog
The vectors below verify exchange-local entry-window evaluation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AE-V01 | nominal valid input for exchange-local entry-window evaluation | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V02 | required field absent for exchange-local entry-window evaluation | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V03 | non-finite numeric value for exchange-local entry-window evaluation | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V04 | boundary value equal to minimum for exchange-local entry-window evaluation | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V05 | value one quantum below minimum for exchange-local entry-window evaluation | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V06 | value one quantum above maximum for exchange-local entry-window evaluation | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V07 | timestamp exactly at start boundary for exchange-local entry-window evaluation | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V08 | timestamp exactly at end boundary for exchange-local entry-window evaluation | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V09 | two otherwise equal candidates for exchange-local entry-window evaluation | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V10 | input collection reversed for exchange-local entry-window evaluation | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V11 | unrelated metadata added for exchange-local entry-window evaluation | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V12 | optional metadata absent for exchange-local entry-window evaluation | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V13 | optional metadata malformed for exchange-local entry-window evaluation | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V14 | immutable input reused for exchange-local entry-window evaluation | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V15 | same input evaluated twice for exchange-local entry-window evaluation | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V16 | same input evaluated concurrently for exchange-local entry-window evaluation | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V17 | event sink raises exception for exchange-local entry-window evaluation | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V18 | unknown enum supplied for exchange-local entry-window evaluation | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V19 | unknown payload field supplied for exchange-local entry-window evaluation | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V20 | unsupported underlying supplied for exchange-local entry-window evaluation | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V21 | snapshot id conflicts with contract identity for exchange-local entry-window evaluation | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V22 | selected contract token absent for exchange-local entry-window evaluation | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V23 | symbol absent but immutable instrument id present for exchange-local entry-window evaluation | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V24 | decimal precision exceeds display precision for exchange-local entry-window evaluation | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V25 | negative zero submitted for exchange-local entry-window evaluation | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V26 | large but finite option chain for exchange-local entry-window evaluation | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V27 | duplicate contract id with same facts for exchange-local entry-window evaluation | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V28 | duplicate contract id with conflicting facts for exchange-local entry-window evaluation | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V29 | score framework rejects factor bundle for exchange-local entry-window evaluation | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V30 | score framework returns sealed score for exchange-local entry-window evaluation | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V31 | RiskProfile prefers no entry for exchange-local entry-window evaluation | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V32 | PortfolioSnapshot contains exposure for exchange-local entry-window evaluation | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V33 | HistoricalSeries is injected for exchange-local entry-window evaluation | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V34 | broker adapter is available in process for exchange-local entry-window evaluation | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V35 | environment contains credentials for exchange-local entry-window evaluation | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V36 | order manager exists in composition for exchange-local entry-window evaluation | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V37 | risk engine exists in composition for exchange-local entry-window evaluation | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V38 | trade decision exists downstream for exchange-local entry-window evaluation | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V39 | serialization round trip for exchange-local entry-window evaluation | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AE-V40 | schema version incompatible for exchange-local entry-window evaluation | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AE-ACCEPT-001:** All forty vectors pass without external calls.
**AE-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AE-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AF — Chain integrity catalog
The vectors below verify option-chain completeness and contract identity. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AF-V01 | nominal valid input for option-chain completeness and contract identity | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V02 | required field absent for option-chain completeness and contract identity | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V03 | non-finite numeric value for option-chain completeness and contract identity | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V04 | boundary value equal to minimum for option-chain completeness and contract identity | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V05 | value one quantum below minimum for option-chain completeness and contract identity | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V06 | value one quantum above maximum for option-chain completeness and contract identity | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V07 | timestamp exactly at start boundary for option-chain completeness and contract identity | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V08 | timestamp exactly at end boundary for option-chain completeness and contract identity | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V09 | two otherwise equal candidates for option-chain completeness and contract identity | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V10 | input collection reversed for option-chain completeness and contract identity | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V11 | unrelated metadata added for option-chain completeness and contract identity | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V12 | optional metadata absent for option-chain completeness and contract identity | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V13 | optional metadata malformed for option-chain completeness and contract identity | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V14 | immutable input reused for option-chain completeness and contract identity | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V15 | same input evaluated twice for option-chain completeness and contract identity | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V16 | same input evaluated concurrently for option-chain completeness and contract identity | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V17 | event sink raises exception for option-chain completeness and contract identity | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V18 | unknown enum supplied for option-chain completeness and contract identity | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V19 | unknown payload field supplied for option-chain completeness and contract identity | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V20 | unsupported underlying supplied for option-chain completeness and contract identity | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V21 | snapshot id conflicts with contract identity for option-chain completeness and contract identity | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V22 | selected contract token absent for option-chain completeness and contract identity | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V23 | symbol absent but immutable instrument id present for option-chain completeness and contract identity | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V24 | decimal precision exceeds display precision for option-chain completeness and contract identity | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V25 | negative zero submitted for option-chain completeness and contract identity | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V26 | large but finite option chain for option-chain completeness and contract identity | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V27 | duplicate contract id with same facts for option-chain completeness and contract identity | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V28 | duplicate contract id with conflicting facts for option-chain completeness and contract identity | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V29 | score framework rejects factor bundle for option-chain completeness and contract identity | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V30 | score framework returns sealed score for option-chain completeness and contract identity | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V31 | RiskProfile prefers no entry for option-chain completeness and contract identity | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V32 | PortfolioSnapshot contains exposure for option-chain completeness and contract identity | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V33 | HistoricalSeries is injected for option-chain completeness and contract identity | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V34 | broker adapter is available in process for option-chain completeness and contract identity | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V35 | environment contains credentials for option-chain completeness and contract identity | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V36 | order manager exists in composition for option-chain completeness and contract identity | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V37 | risk engine exists in composition for option-chain completeness and contract identity | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V38 | trade decision exists downstream for option-chain completeness and contract identity | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V39 | serialization round trip for option-chain completeness and contract identity | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AF-V40 | schema version incompatible for option-chain completeness and contract identity | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AF-ACCEPT-001:** All forty vectors pass without external calls.
**AF-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AF-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AG — Quote integrity catalog
The vectors below verify bid, ask, midpoint, and spread validation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AG-V01 | nominal valid input for bid, ask, midpoint, and spread validation | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V02 | required field absent for bid, ask, midpoint, and spread validation | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V03 | non-finite numeric value for bid, ask, midpoint, and spread validation | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V04 | boundary value equal to minimum for bid, ask, midpoint, and spread validation | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V05 | value one quantum below minimum for bid, ask, midpoint, and spread validation | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V06 | value one quantum above maximum for bid, ask, midpoint, and spread validation | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V07 | timestamp exactly at start boundary for bid, ask, midpoint, and spread validation | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V08 | timestamp exactly at end boundary for bid, ask, midpoint, and spread validation | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V09 | two otherwise equal candidates for bid, ask, midpoint, and spread validation | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V10 | input collection reversed for bid, ask, midpoint, and spread validation | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V11 | unrelated metadata added for bid, ask, midpoint, and spread validation | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V12 | optional metadata absent for bid, ask, midpoint, and spread validation | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V13 | optional metadata malformed for bid, ask, midpoint, and spread validation | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V14 | immutable input reused for bid, ask, midpoint, and spread validation | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V15 | same input evaluated twice for bid, ask, midpoint, and spread validation | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V16 | same input evaluated concurrently for bid, ask, midpoint, and spread validation | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V17 | event sink raises exception for bid, ask, midpoint, and spread validation | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V18 | unknown enum supplied for bid, ask, midpoint, and spread validation | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V19 | unknown payload field supplied for bid, ask, midpoint, and spread validation | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V20 | unsupported underlying supplied for bid, ask, midpoint, and spread validation | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V21 | snapshot id conflicts with contract identity for bid, ask, midpoint, and spread validation | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V22 | selected contract token absent for bid, ask, midpoint, and spread validation | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V23 | symbol absent but immutable instrument id present for bid, ask, midpoint, and spread validation | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V24 | decimal precision exceeds display precision for bid, ask, midpoint, and spread validation | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V25 | negative zero submitted for bid, ask, midpoint, and spread validation | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V26 | large but finite option chain for bid, ask, midpoint, and spread validation | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V27 | duplicate contract id with same facts for bid, ask, midpoint, and spread validation | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V28 | duplicate contract id with conflicting facts for bid, ask, midpoint, and spread validation | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V29 | score framework rejects factor bundle for bid, ask, midpoint, and spread validation | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V30 | score framework returns sealed score for bid, ask, midpoint, and spread validation | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V31 | RiskProfile prefers no entry for bid, ask, midpoint, and spread validation | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V32 | PortfolioSnapshot contains exposure for bid, ask, midpoint, and spread validation | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V33 | HistoricalSeries is injected for bid, ask, midpoint, and spread validation | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V34 | broker adapter is available in process for bid, ask, midpoint, and spread validation | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V35 | environment contains credentials for bid, ask, midpoint, and spread validation | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V36 | order manager exists in composition for bid, ask, midpoint, and spread validation | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V37 | risk engine exists in composition for bid, ask, midpoint, and spread validation | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V38 | trade decision exists downstream for bid, ask, midpoint, and spread validation | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V39 | serialization round trip for bid, ask, midpoint, and spread validation | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AG-V40 | schema version incompatible for bid, ask, midpoint, and spread validation | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AG-ACCEPT-001:** All forty vectors pass without external calls.
**AG-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AG-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AH — Open-interest catalog
The vectors below verify open-interest liquidity policy. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AH-V01 | nominal valid input for open-interest liquidity policy | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V02 | required field absent for open-interest liquidity policy | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V03 | non-finite numeric value for open-interest liquidity policy | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V04 | boundary value equal to minimum for open-interest liquidity policy | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V05 | value one quantum below minimum for open-interest liquidity policy | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V06 | value one quantum above maximum for open-interest liquidity policy | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V07 | timestamp exactly at start boundary for open-interest liquidity policy | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V08 | timestamp exactly at end boundary for open-interest liquidity policy | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V09 | two otherwise equal candidates for open-interest liquidity policy | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V10 | input collection reversed for open-interest liquidity policy | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V11 | unrelated metadata added for open-interest liquidity policy | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V12 | optional metadata absent for open-interest liquidity policy | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V13 | optional metadata malformed for open-interest liquidity policy | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V14 | immutable input reused for open-interest liquidity policy | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V15 | same input evaluated twice for open-interest liquidity policy | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V16 | same input evaluated concurrently for open-interest liquidity policy | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V17 | event sink raises exception for open-interest liquidity policy | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V18 | unknown enum supplied for open-interest liquidity policy | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V19 | unknown payload field supplied for open-interest liquidity policy | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V20 | unsupported underlying supplied for open-interest liquidity policy | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V21 | snapshot id conflicts with contract identity for open-interest liquidity policy | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V22 | selected contract token absent for open-interest liquidity policy | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V23 | symbol absent but immutable instrument id present for open-interest liquidity policy | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V24 | decimal precision exceeds display precision for open-interest liquidity policy | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V25 | negative zero submitted for open-interest liquidity policy | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V26 | large but finite option chain for open-interest liquidity policy | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V27 | duplicate contract id with same facts for open-interest liquidity policy | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V28 | duplicate contract id with conflicting facts for open-interest liquidity policy | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V29 | score framework rejects factor bundle for open-interest liquidity policy | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V30 | score framework returns sealed score for open-interest liquidity policy | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V31 | RiskProfile prefers no entry for open-interest liquidity policy | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V32 | PortfolioSnapshot contains exposure for open-interest liquidity policy | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V33 | HistoricalSeries is injected for open-interest liquidity policy | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V34 | broker adapter is available in process for open-interest liquidity policy | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V35 | environment contains credentials for open-interest liquidity policy | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V36 | order manager exists in composition for open-interest liquidity policy | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V37 | risk engine exists in composition for open-interest liquidity policy | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V38 | trade decision exists downstream for open-interest liquidity policy | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V39 | serialization round trip for open-interest liquidity policy | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AH-V40 | schema version incompatible for open-interest liquidity policy | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AH-ACCEPT-001:** All forty vectors pass without external calls.
**AH-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AH-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AI — Volume catalog
The vectors below verify traded-volume liquidity policy. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AI-V01 | nominal valid input for traded-volume liquidity policy | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V02 | required field absent for traded-volume liquidity policy | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V03 | non-finite numeric value for traded-volume liquidity policy | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V04 | boundary value equal to minimum for traded-volume liquidity policy | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V05 | value one quantum below minimum for traded-volume liquidity policy | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V06 | value one quantum above maximum for traded-volume liquidity policy | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V07 | timestamp exactly at start boundary for traded-volume liquidity policy | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V08 | timestamp exactly at end boundary for traded-volume liquidity policy | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V09 | two otherwise equal candidates for traded-volume liquidity policy | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V10 | input collection reversed for traded-volume liquidity policy | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V11 | unrelated metadata added for traded-volume liquidity policy | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V12 | optional metadata absent for traded-volume liquidity policy | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V13 | optional metadata malformed for traded-volume liquidity policy | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V14 | immutable input reused for traded-volume liquidity policy | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V15 | same input evaluated twice for traded-volume liquidity policy | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V16 | same input evaluated concurrently for traded-volume liquidity policy | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V17 | event sink raises exception for traded-volume liquidity policy | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V18 | unknown enum supplied for traded-volume liquidity policy | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V19 | unknown payload field supplied for traded-volume liquidity policy | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V20 | unsupported underlying supplied for traded-volume liquidity policy | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V21 | snapshot id conflicts with contract identity for traded-volume liquidity policy | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V22 | selected contract token absent for traded-volume liquidity policy | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V23 | symbol absent but immutable instrument id present for traded-volume liquidity policy | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V24 | decimal precision exceeds display precision for traded-volume liquidity policy | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V25 | negative zero submitted for traded-volume liquidity policy | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V26 | large but finite option chain for traded-volume liquidity policy | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V27 | duplicate contract id with same facts for traded-volume liquidity policy | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V28 | duplicate contract id with conflicting facts for traded-volume liquidity policy | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V29 | score framework rejects factor bundle for traded-volume liquidity policy | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V30 | score framework returns sealed score for traded-volume liquidity policy | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V31 | RiskProfile prefers no entry for traded-volume liquidity policy | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V32 | PortfolioSnapshot contains exposure for traded-volume liquidity policy | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V33 | HistoricalSeries is injected for traded-volume liquidity policy | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V34 | broker adapter is available in process for traded-volume liquidity policy | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V35 | environment contains credentials for traded-volume liquidity policy | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V36 | order manager exists in composition for traded-volume liquidity policy | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V37 | risk engine exists in composition for traded-volume liquidity policy | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V38 | trade decision exists downstream for traded-volume liquidity policy | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V39 | serialization round trip for traded-volume liquidity policy | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AI-V40 | schema version incompatible for traded-volume liquidity policy | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AI-ACCEPT-001:** All forty vectors pass without external calls.
**AI-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AI-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AJ — Expiry selection catalog
The vectors below verify DTE and shared-expiry selection. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AJ-V01 | nominal valid input for DTE and shared-expiry selection | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V02 | required field absent for DTE and shared-expiry selection | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V03 | non-finite numeric value for DTE and shared-expiry selection | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V04 | boundary value equal to minimum for DTE and shared-expiry selection | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V05 | value one quantum below minimum for DTE and shared-expiry selection | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V06 | value one quantum above maximum for DTE and shared-expiry selection | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V07 | timestamp exactly at start boundary for DTE and shared-expiry selection | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V08 | timestamp exactly at end boundary for DTE and shared-expiry selection | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V09 | two otherwise equal candidates for DTE and shared-expiry selection | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V10 | input collection reversed for DTE and shared-expiry selection | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V11 | unrelated metadata added for DTE and shared-expiry selection | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V12 | optional metadata absent for DTE and shared-expiry selection | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V13 | optional metadata malformed for DTE and shared-expiry selection | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V14 | immutable input reused for DTE and shared-expiry selection | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V15 | same input evaluated twice for DTE and shared-expiry selection | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V16 | same input evaluated concurrently for DTE and shared-expiry selection | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V17 | event sink raises exception for DTE and shared-expiry selection | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V18 | unknown enum supplied for DTE and shared-expiry selection | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V19 | unknown payload field supplied for DTE and shared-expiry selection | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V20 | unsupported underlying supplied for DTE and shared-expiry selection | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V21 | snapshot id conflicts with contract identity for DTE and shared-expiry selection | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V22 | selected contract token absent for DTE and shared-expiry selection | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V23 | symbol absent but immutable instrument id present for DTE and shared-expiry selection | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V24 | decimal precision exceeds display precision for DTE and shared-expiry selection | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V25 | negative zero submitted for DTE and shared-expiry selection | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V26 | large but finite option chain for DTE and shared-expiry selection | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V27 | duplicate contract id with same facts for DTE and shared-expiry selection | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V28 | duplicate contract id with conflicting facts for DTE and shared-expiry selection | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V29 | score framework rejects factor bundle for DTE and shared-expiry selection | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V30 | score framework returns sealed score for DTE and shared-expiry selection | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V31 | RiskProfile prefers no entry for DTE and shared-expiry selection | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V32 | PortfolioSnapshot contains exposure for DTE and shared-expiry selection | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V33 | HistoricalSeries is injected for DTE and shared-expiry selection | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V34 | broker adapter is available in process for DTE and shared-expiry selection | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V35 | environment contains credentials for DTE and shared-expiry selection | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V36 | order manager exists in composition for DTE and shared-expiry selection | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V37 | risk engine exists in composition for DTE and shared-expiry selection | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V38 | trade decision exists downstream for DTE and shared-expiry selection | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V39 | serialization round trip for DTE and shared-expiry selection | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AJ-V40 | schema version incompatible for DTE and shared-expiry selection | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AJ-ACCEPT-001:** All forty vectors pass without external calls.
**AJ-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AJ-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AK — Call candidate catalog
The vectors below verify OTM call candidate filtering and ranking. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AK-V01 | nominal valid input for OTM call candidate filtering and ranking | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V02 | required field absent for OTM call candidate filtering and ranking | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V03 | non-finite numeric value for OTM call candidate filtering and ranking | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V04 | boundary value equal to minimum for OTM call candidate filtering and ranking | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V05 | value one quantum below minimum for OTM call candidate filtering and ranking | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V06 | value one quantum above maximum for OTM call candidate filtering and ranking | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V07 | timestamp exactly at start boundary for OTM call candidate filtering and ranking | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V08 | timestamp exactly at end boundary for OTM call candidate filtering and ranking | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V09 | two otherwise equal candidates for OTM call candidate filtering and ranking | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V10 | input collection reversed for OTM call candidate filtering and ranking | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V11 | unrelated metadata added for OTM call candidate filtering and ranking | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V12 | optional metadata absent for OTM call candidate filtering and ranking | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V13 | optional metadata malformed for OTM call candidate filtering and ranking | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V14 | immutable input reused for OTM call candidate filtering and ranking | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V15 | same input evaluated twice for OTM call candidate filtering and ranking | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V16 | same input evaluated concurrently for OTM call candidate filtering and ranking | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V17 | event sink raises exception for OTM call candidate filtering and ranking | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V18 | unknown enum supplied for OTM call candidate filtering and ranking | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V19 | unknown payload field supplied for OTM call candidate filtering and ranking | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V20 | unsupported underlying supplied for OTM call candidate filtering and ranking | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V21 | snapshot id conflicts with contract identity for OTM call candidate filtering and ranking | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V22 | selected contract token absent for OTM call candidate filtering and ranking | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V23 | symbol absent but immutable instrument id present for OTM call candidate filtering and ranking | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V24 | decimal precision exceeds display precision for OTM call candidate filtering and ranking | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V25 | negative zero submitted for OTM call candidate filtering and ranking | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V26 | large but finite option chain for OTM call candidate filtering and ranking | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V27 | duplicate contract id with same facts for OTM call candidate filtering and ranking | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V28 | duplicate contract id with conflicting facts for OTM call candidate filtering and ranking | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V29 | score framework rejects factor bundle for OTM call candidate filtering and ranking | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V30 | score framework returns sealed score for OTM call candidate filtering and ranking | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V31 | RiskProfile prefers no entry for OTM call candidate filtering and ranking | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V32 | PortfolioSnapshot contains exposure for OTM call candidate filtering and ranking | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V33 | HistoricalSeries is injected for OTM call candidate filtering and ranking | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V34 | broker adapter is available in process for OTM call candidate filtering and ranking | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V35 | environment contains credentials for OTM call candidate filtering and ranking | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V36 | order manager exists in composition for OTM call candidate filtering and ranking | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V37 | risk engine exists in composition for OTM call candidate filtering and ranking | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V38 | trade decision exists downstream for OTM call candidate filtering and ranking | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V39 | serialization round trip for OTM call candidate filtering and ranking | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AK-V40 | schema version incompatible for OTM call candidate filtering and ranking | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AK-ACCEPT-001:** All forty vectors pass without external calls.
**AK-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AK-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AL — Put candidate catalog
The vectors below verify OTM put candidate filtering and ranking. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AL-V01 | nominal valid input for OTM put candidate filtering and ranking | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V02 | required field absent for OTM put candidate filtering and ranking | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V03 | non-finite numeric value for OTM put candidate filtering and ranking | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V04 | boundary value equal to minimum for OTM put candidate filtering and ranking | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V05 | value one quantum below minimum for OTM put candidate filtering and ranking | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V06 | value one quantum above maximum for OTM put candidate filtering and ranking | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V07 | timestamp exactly at start boundary for OTM put candidate filtering and ranking | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V08 | timestamp exactly at end boundary for OTM put candidate filtering and ranking | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V09 | two otherwise equal candidates for OTM put candidate filtering and ranking | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V10 | input collection reversed for OTM put candidate filtering and ranking | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V11 | unrelated metadata added for OTM put candidate filtering and ranking | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V12 | optional metadata absent for OTM put candidate filtering and ranking | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V13 | optional metadata malformed for OTM put candidate filtering and ranking | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V14 | immutable input reused for OTM put candidate filtering and ranking | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V15 | same input evaluated twice for OTM put candidate filtering and ranking | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V16 | same input evaluated concurrently for OTM put candidate filtering and ranking | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V17 | event sink raises exception for OTM put candidate filtering and ranking | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V18 | unknown enum supplied for OTM put candidate filtering and ranking | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V19 | unknown payload field supplied for OTM put candidate filtering and ranking | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V20 | unsupported underlying supplied for OTM put candidate filtering and ranking | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V21 | snapshot id conflicts with contract identity for OTM put candidate filtering and ranking | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V22 | selected contract token absent for OTM put candidate filtering and ranking | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V23 | symbol absent but immutable instrument id present for OTM put candidate filtering and ranking | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V24 | decimal precision exceeds display precision for OTM put candidate filtering and ranking | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V25 | negative zero submitted for OTM put candidate filtering and ranking | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V26 | large but finite option chain for OTM put candidate filtering and ranking | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V27 | duplicate contract id with same facts for OTM put candidate filtering and ranking | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V28 | duplicate contract id with conflicting facts for OTM put candidate filtering and ranking | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V29 | score framework rejects factor bundle for OTM put candidate filtering and ranking | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V30 | score framework returns sealed score for OTM put candidate filtering and ranking | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V31 | RiskProfile prefers no entry for OTM put candidate filtering and ranking | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V32 | PortfolioSnapshot contains exposure for OTM put candidate filtering and ranking | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V33 | HistoricalSeries is injected for OTM put candidate filtering and ranking | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V34 | broker adapter is available in process for OTM put candidate filtering and ranking | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V35 | environment contains credentials for OTM put candidate filtering and ranking | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V36 | order manager exists in composition for OTM put candidate filtering and ranking | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V37 | risk engine exists in composition for OTM put candidate filtering and ranking | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V38 | trade decision exists downstream for OTM put candidate filtering and ranking | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V39 | serialization round trip for OTM put candidate filtering and ranking | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AL-V40 | schema version incompatible for OTM put candidate filtering and ranking | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AL-ACCEPT-001:** All forty vectors pass without external calls.
**AL-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AL-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AM — Tie-break catalog
The vectors below verify deterministic strike ranking tie breaks. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AM-V01 | nominal valid input for deterministic strike ranking tie breaks | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V02 | required field absent for deterministic strike ranking tie breaks | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V03 | non-finite numeric value for deterministic strike ranking tie breaks | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V04 | boundary value equal to minimum for deterministic strike ranking tie breaks | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V05 | value one quantum below minimum for deterministic strike ranking tie breaks | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V06 | value one quantum above maximum for deterministic strike ranking tie breaks | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V07 | timestamp exactly at start boundary for deterministic strike ranking tie breaks | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V08 | timestamp exactly at end boundary for deterministic strike ranking tie breaks | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V09 | two otherwise equal candidates for deterministic strike ranking tie breaks | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V10 | input collection reversed for deterministic strike ranking tie breaks | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V11 | unrelated metadata added for deterministic strike ranking tie breaks | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V12 | optional metadata absent for deterministic strike ranking tie breaks | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V13 | optional metadata malformed for deterministic strike ranking tie breaks | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V14 | immutable input reused for deterministic strike ranking tie breaks | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V15 | same input evaluated twice for deterministic strike ranking tie breaks | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V16 | same input evaluated concurrently for deterministic strike ranking tie breaks | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V17 | event sink raises exception for deterministic strike ranking tie breaks | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V18 | unknown enum supplied for deterministic strike ranking tie breaks | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V19 | unknown payload field supplied for deterministic strike ranking tie breaks | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V20 | unsupported underlying supplied for deterministic strike ranking tie breaks | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V21 | snapshot id conflicts with contract identity for deterministic strike ranking tie breaks | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V22 | selected contract token absent for deterministic strike ranking tie breaks | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V23 | symbol absent but immutable instrument id present for deterministic strike ranking tie breaks | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V24 | decimal precision exceeds display precision for deterministic strike ranking tie breaks | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V25 | negative zero submitted for deterministic strike ranking tie breaks | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V26 | large but finite option chain for deterministic strike ranking tie breaks | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V27 | duplicate contract id with same facts for deterministic strike ranking tie breaks | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V28 | duplicate contract id with conflicting facts for deterministic strike ranking tie breaks | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V29 | score framework rejects factor bundle for deterministic strike ranking tie breaks | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V30 | score framework returns sealed score for deterministic strike ranking tie breaks | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V31 | RiskProfile prefers no entry for deterministic strike ranking tie breaks | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V32 | PortfolioSnapshot contains exposure for deterministic strike ranking tie breaks | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V33 | HistoricalSeries is injected for deterministic strike ranking tie breaks | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V34 | broker adapter is available in process for deterministic strike ranking tie breaks | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V35 | environment contains credentials for deterministic strike ranking tie breaks | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V36 | order manager exists in composition for deterministic strike ranking tie breaks | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V37 | risk engine exists in composition for deterministic strike ranking tie breaks | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V38 | trade decision exists downstream for deterministic strike ranking tie breaks | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V39 | serialization round trip for deterministic strike ranking tie breaks | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AM-V40 | schema version incompatible for deterministic strike ranking tie breaks | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AM-ACCEPT-001:** All forty vectors pass without external calls.
**AM-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AM-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AN — Premium-policy catalog
The vectors below verify MID and ASK premium computation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AN-V01 | nominal valid input for MID and ASK premium computation | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V02 | required field absent for MID and ASK premium computation | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V03 | non-finite numeric value for MID and ASK premium computation | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V04 | boundary value equal to minimum for MID and ASK premium computation | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V05 | value one quantum below minimum for MID and ASK premium computation | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V06 | value one quantum above maximum for MID and ASK premium computation | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V07 | timestamp exactly at start boundary for MID and ASK premium computation | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V08 | timestamp exactly at end boundary for MID and ASK premium computation | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V09 | two otherwise equal candidates for MID and ASK premium computation | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V10 | input collection reversed for MID and ASK premium computation | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V11 | unrelated metadata added for MID and ASK premium computation | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V12 | optional metadata absent for MID and ASK premium computation | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V13 | optional metadata malformed for MID and ASK premium computation | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V14 | immutable input reused for MID and ASK premium computation | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V15 | same input evaluated twice for MID and ASK premium computation | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V16 | same input evaluated concurrently for MID and ASK premium computation | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V17 | event sink raises exception for MID and ASK premium computation | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V18 | unknown enum supplied for MID and ASK premium computation | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V19 | unknown payload field supplied for MID and ASK premium computation | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V20 | unsupported underlying supplied for MID and ASK premium computation | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V21 | snapshot id conflicts with contract identity for MID and ASK premium computation | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V22 | selected contract token absent for MID and ASK premium computation | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V23 | symbol absent but immutable instrument id present for MID and ASK premium computation | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V24 | decimal precision exceeds display precision for MID and ASK premium computation | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V25 | negative zero submitted for MID and ASK premium computation | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V26 | large but finite option chain for MID and ASK premium computation | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V27 | duplicate contract id with same facts for MID and ASK premium computation | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V28 | duplicate contract id with conflicting facts for MID and ASK premium computation | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V29 | score framework rejects factor bundle for MID and ASK premium computation | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V30 | score framework returns sealed score for MID and ASK premium computation | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V31 | RiskProfile prefers no entry for MID and ASK premium computation | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V32 | PortfolioSnapshot contains exposure for MID and ASK premium computation | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V33 | HistoricalSeries is injected for MID and ASK premium computation | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V34 | broker adapter is available in process for MID and ASK premium computation | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V35 | environment contains credentials for MID and ASK premium computation | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V36 | order manager exists in composition for MID and ASK premium computation | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V37 | risk engine exists in composition for MID and ASK premium computation | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V38 | trade decision exists downstream for MID and ASK premium computation | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V39 | serialization round trip for MID and ASK premium computation | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AN-V40 | schema version incompatible for MID and ASK premium computation | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AN-ACCEPT-001:** All forty vectors pass without external calls.
**AN-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AN-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AO — Credit threshold catalog
The vectors below verify net-credit and minimum-premium validation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AO-V01 | nominal valid input for net-credit and minimum-premium validation | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V02 | required field absent for net-credit and minimum-premium validation | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V03 | non-finite numeric value for net-credit and minimum-premium validation | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V04 | boundary value equal to minimum for net-credit and minimum-premium validation | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V05 | value one quantum below minimum for net-credit and minimum-premium validation | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V06 | value one quantum above maximum for net-credit and minimum-premium validation | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V07 | timestamp exactly at start boundary for net-credit and minimum-premium validation | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V08 | timestamp exactly at end boundary for net-credit and minimum-premium validation | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V09 | two otherwise equal candidates for net-credit and minimum-premium validation | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V10 | input collection reversed for net-credit and minimum-premium validation | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V11 | unrelated metadata added for net-credit and minimum-premium validation | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V12 | optional metadata absent for net-credit and minimum-premium validation | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V13 | optional metadata malformed for net-credit and minimum-premium validation | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V14 | immutable input reused for net-credit and minimum-premium validation | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V15 | same input evaluated twice for net-credit and minimum-premium validation | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V16 | same input evaluated concurrently for net-credit and minimum-premium validation | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V17 | event sink raises exception for net-credit and minimum-premium validation | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V18 | unknown enum supplied for net-credit and minimum-premium validation | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V19 | unknown payload field supplied for net-credit and minimum-premium validation | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V20 | unsupported underlying supplied for net-credit and minimum-premium validation | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V21 | snapshot id conflicts with contract identity for net-credit and minimum-premium validation | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V22 | selected contract token absent for net-credit and minimum-premium validation | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V23 | symbol absent but immutable instrument id present for net-credit and minimum-premium validation | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V24 | decimal precision exceeds display precision for net-credit and minimum-premium validation | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V25 | negative zero submitted for net-credit and minimum-premium validation | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V26 | large but finite option chain for net-credit and minimum-premium validation | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V27 | duplicate contract id with same facts for net-credit and minimum-premium validation | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V28 | duplicate contract id with conflicting facts for net-credit and minimum-premium validation | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V29 | score framework rejects factor bundle for net-credit and minimum-premium validation | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V30 | score framework returns sealed score for net-credit and minimum-premium validation | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V31 | RiskProfile prefers no entry for net-credit and minimum-premium validation | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V32 | PortfolioSnapshot contains exposure for net-credit and minimum-premium validation | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V33 | HistoricalSeries is injected for net-credit and minimum-premium validation | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V34 | broker adapter is available in process for net-credit and minimum-premium validation | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V35 | environment contains credentials for net-credit and minimum-premium validation | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V36 | order manager exists in composition for net-credit and minimum-premium validation | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V37 | risk engine exists in composition for net-credit and minimum-premium validation | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V38 | trade decision exists downstream for net-credit and minimum-premium validation | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V39 | serialization round trip for net-credit and minimum-premium validation | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AO-V40 | schema version incompatible for net-credit and minimum-premium validation | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AO-ACCEPT-001:** All forty vectors pass without external calls.
**AO-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AO-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AP — POP heuristic catalog
The vectors below verify delta-derived probability-of-profit estimates. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AP-V01 | nominal valid input for delta-derived probability-of-profit estimates | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V02 | required field absent for delta-derived probability-of-profit estimates | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V03 | non-finite numeric value for delta-derived probability-of-profit estimates | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V04 | boundary value equal to minimum for delta-derived probability-of-profit estimates | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V05 | value one quantum below minimum for delta-derived probability-of-profit estimates | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V06 | value one quantum above maximum for delta-derived probability-of-profit estimates | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V07 | timestamp exactly at start boundary for delta-derived probability-of-profit estimates | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V08 | timestamp exactly at end boundary for delta-derived probability-of-profit estimates | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V09 | two otherwise equal candidates for delta-derived probability-of-profit estimates | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V10 | input collection reversed for delta-derived probability-of-profit estimates | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V11 | unrelated metadata added for delta-derived probability-of-profit estimates | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V12 | optional metadata absent for delta-derived probability-of-profit estimates | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V13 | optional metadata malformed for delta-derived probability-of-profit estimates | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V14 | immutable input reused for delta-derived probability-of-profit estimates | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V15 | same input evaluated twice for delta-derived probability-of-profit estimates | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V16 | same input evaluated concurrently for delta-derived probability-of-profit estimates | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V17 | event sink raises exception for delta-derived probability-of-profit estimates | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V18 | unknown enum supplied for delta-derived probability-of-profit estimates | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V19 | unknown payload field supplied for delta-derived probability-of-profit estimates | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V20 | unsupported underlying supplied for delta-derived probability-of-profit estimates | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V21 | snapshot id conflicts with contract identity for delta-derived probability-of-profit estimates | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V22 | selected contract token absent for delta-derived probability-of-profit estimates | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V23 | symbol absent but immutable instrument id present for delta-derived probability-of-profit estimates | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V24 | decimal precision exceeds display precision for delta-derived probability-of-profit estimates | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V25 | negative zero submitted for delta-derived probability-of-profit estimates | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V26 | large but finite option chain for delta-derived probability-of-profit estimates | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V27 | duplicate contract id with same facts for delta-derived probability-of-profit estimates | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V28 | duplicate contract id with conflicting facts for delta-derived probability-of-profit estimates | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V29 | score framework rejects factor bundle for delta-derived probability-of-profit estimates | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V30 | score framework returns sealed score for delta-derived probability-of-profit estimates | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V31 | RiskProfile prefers no entry for delta-derived probability-of-profit estimates | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V32 | PortfolioSnapshot contains exposure for delta-derived probability-of-profit estimates | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V33 | HistoricalSeries is injected for delta-derived probability-of-profit estimates | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V34 | broker adapter is available in process for delta-derived probability-of-profit estimates | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V35 | environment contains credentials for delta-derived probability-of-profit estimates | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V36 | order manager exists in composition for delta-derived probability-of-profit estimates | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V37 | risk engine exists in composition for delta-derived probability-of-profit estimates | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V38 | trade decision exists downstream for delta-derived probability-of-profit estimates | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V39 | serialization round trip for delta-derived probability-of-profit estimates | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AP-V40 | schema version incompatible for delta-derived probability-of-profit estimates | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AP-ACCEPT-001:** All forty vectors pass without external calls.
**AP-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AP-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AQ — Undefined-risk catalog
The vectors below verify naked risk disclosure and max-loss semantics. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AQ-V01 | nominal valid input for naked risk disclosure and max-loss semantics | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V02 | required field absent for naked risk disclosure and max-loss semantics | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V03 | non-finite numeric value for naked risk disclosure and max-loss semantics | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V04 | boundary value equal to minimum for naked risk disclosure and max-loss semantics | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V05 | value one quantum below minimum for naked risk disclosure and max-loss semantics | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V06 | value one quantum above maximum for naked risk disclosure and max-loss semantics | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V07 | timestamp exactly at start boundary for naked risk disclosure and max-loss semantics | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V08 | timestamp exactly at end boundary for naked risk disclosure and max-loss semantics | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V09 | two otherwise equal candidates for naked risk disclosure and max-loss semantics | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V10 | input collection reversed for naked risk disclosure and max-loss semantics | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V11 | unrelated metadata added for naked risk disclosure and max-loss semantics | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V12 | optional metadata absent for naked risk disclosure and max-loss semantics | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V13 | optional metadata malformed for naked risk disclosure and max-loss semantics | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V14 | immutable input reused for naked risk disclosure and max-loss semantics | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V15 | same input evaluated twice for naked risk disclosure and max-loss semantics | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V16 | same input evaluated concurrently for naked risk disclosure and max-loss semantics | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V17 | event sink raises exception for naked risk disclosure and max-loss semantics | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V18 | unknown enum supplied for naked risk disclosure and max-loss semantics | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V19 | unknown payload field supplied for naked risk disclosure and max-loss semantics | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V20 | unsupported underlying supplied for naked risk disclosure and max-loss semantics | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V21 | snapshot id conflicts with contract identity for naked risk disclosure and max-loss semantics | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V22 | selected contract token absent for naked risk disclosure and max-loss semantics | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V23 | symbol absent but immutable instrument id present for naked risk disclosure and max-loss semantics | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V24 | decimal precision exceeds display precision for naked risk disclosure and max-loss semantics | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V25 | negative zero submitted for naked risk disclosure and max-loss semantics | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V26 | large but finite option chain for naked risk disclosure and max-loss semantics | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V27 | duplicate contract id with same facts for naked risk disclosure and max-loss semantics | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V28 | duplicate contract id with conflicting facts for naked risk disclosure and max-loss semantics | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V29 | score framework rejects factor bundle for naked risk disclosure and max-loss semantics | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V30 | score framework returns sealed score for naked risk disclosure and max-loss semantics | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V31 | RiskProfile prefers no entry for naked risk disclosure and max-loss semantics | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V32 | PortfolioSnapshot contains exposure for naked risk disclosure and max-loss semantics | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V33 | HistoricalSeries is injected for naked risk disclosure and max-loss semantics | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V34 | broker adapter is available in process for naked risk disclosure and max-loss semantics | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V35 | environment contains credentials for naked risk disclosure and max-loss semantics | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V36 | order manager exists in composition for naked risk disclosure and max-loss semantics | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V37 | risk engine exists in composition for naked risk disclosure and max-loss semantics | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V38 | trade decision exists downstream for naked risk disclosure and max-loss semantics | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V39 | serialization round trip for naked risk disclosure and max-loss semantics | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AQ-V40 | schema version incompatible for naked risk disclosure and max-loss semantics | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AQ-ACCEPT-001:** All forty vectors pass without external calls.
**AQ-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AQ-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AR — Scoring bundle catalog
The vectors below verify PREMIUM_SELLING factor extraction. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AR-V01 | nominal valid input for PREMIUM_SELLING factor extraction | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V02 | required field absent for PREMIUM_SELLING factor extraction | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V03 | non-finite numeric value for PREMIUM_SELLING factor extraction | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V04 | boundary value equal to minimum for PREMIUM_SELLING factor extraction | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V05 | value one quantum below minimum for PREMIUM_SELLING factor extraction | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V06 | value one quantum above maximum for PREMIUM_SELLING factor extraction | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V07 | timestamp exactly at start boundary for PREMIUM_SELLING factor extraction | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V08 | timestamp exactly at end boundary for PREMIUM_SELLING factor extraction | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V09 | two otherwise equal candidates for PREMIUM_SELLING factor extraction | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V10 | input collection reversed for PREMIUM_SELLING factor extraction | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V11 | unrelated metadata added for PREMIUM_SELLING factor extraction | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V12 | optional metadata absent for PREMIUM_SELLING factor extraction | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V13 | optional metadata malformed for PREMIUM_SELLING factor extraction | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V14 | immutable input reused for PREMIUM_SELLING factor extraction | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V15 | same input evaluated twice for PREMIUM_SELLING factor extraction | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V16 | same input evaluated concurrently for PREMIUM_SELLING factor extraction | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V17 | event sink raises exception for PREMIUM_SELLING factor extraction | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V18 | unknown enum supplied for PREMIUM_SELLING factor extraction | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V19 | unknown payload field supplied for PREMIUM_SELLING factor extraction | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V20 | unsupported underlying supplied for PREMIUM_SELLING factor extraction | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V21 | snapshot id conflicts with contract identity for PREMIUM_SELLING factor extraction | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V22 | selected contract token absent for PREMIUM_SELLING factor extraction | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V23 | symbol absent but immutable instrument id present for PREMIUM_SELLING factor extraction | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V24 | decimal precision exceeds display precision for PREMIUM_SELLING factor extraction | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V25 | negative zero submitted for PREMIUM_SELLING factor extraction | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V26 | large but finite option chain for PREMIUM_SELLING factor extraction | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V27 | duplicate contract id with same facts for PREMIUM_SELLING factor extraction | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V28 | duplicate contract id with conflicting facts for PREMIUM_SELLING factor extraction | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V29 | score framework rejects factor bundle for PREMIUM_SELLING factor extraction | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V30 | score framework returns sealed score for PREMIUM_SELLING factor extraction | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V31 | RiskProfile prefers no entry for PREMIUM_SELLING factor extraction | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V32 | PortfolioSnapshot contains exposure for PREMIUM_SELLING factor extraction | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V33 | HistoricalSeries is injected for PREMIUM_SELLING factor extraction | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V34 | broker adapter is available in process for PREMIUM_SELLING factor extraction | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V35 | environment contains credentials for PREMIUM_SELLING factor extraction | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V36 | order manager exists in composition for PREMIUM_SELLING factor extraction | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V37 | risk engine exists in composition for PREMIUM_SELLING factor extraction | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V38 | trade decision exists downstream for PREMIUM_SELLING factor extraction | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V39 | serialization round trip for PREMIUM_SELLING factor extraction | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AR-V40 | schema version incompatible for PREMIUM_SELLING factor extraction | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AR-ACCEPT-001:** All forty vectors pass without external calls.
**AR-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AR-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AS — Confidence catalog
The vectors below verify score confidence and abstention semantics. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AS-V01 | nominal valid input for score confidence and abstention semantics | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V02 | required field absent for score confidence and abstention semantics | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V03 | non-finite numeric value for score confidence and abstention semantics | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V04 | boundary value equal to minimum for score confidence and abstention semantics | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V05 | value one quantum below minimum for score confidence and abstention semantics | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V06 | value one quantum above maximum for score confidence and abstention semantics | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V07 | timestamp exactly at start boundary for score confidence and abstention semantics | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V08 | timestamp exactly at end boundary for score confidence and abstention semantics | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V09 | two otherwise equal candidates for score confidence and abstention semantics | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V10 | input collection reversed for score confidence and abstention semantics | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V11 | unrelated metadata added for score confidence and abstention semantics | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V12 | optional metadata absent for score confidence and abstention semantics | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V13 | optional metadata malformed for score confidence and abstention semantics | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V14 | immutable input reused for score confidence and abstention semantics | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V15 | same input evaluated twice for score confidence and abstention semantics | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V16 | same input evaluated concurrently for score confidence and abstention semantics | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V17 | event sink raises exception for score confidence and abstention semantics | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V18 | unknown enum supplied for score confidence and abstention semantics | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V19 | unknown payload field supplied for score confidence and abstention semantics | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V20 | unsupported underlying supplied for score confidence and abstention semantics | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V21 | snapshot id conflicts with contract identity for score confidence and abstention semantics | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V22 | selected contract token absent for score confidence and abstention semantics | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V23 | symbol absent but immutable instrument id present for score confidence and abstention semantics | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V24 | decimal precision exceeds display precision for score confidence and abstention semantics | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V25 | negative zero submitted for score confidence and abstention semantics | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V26 | large but finite option chain for score confidence and abstention semantics | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V27 | duplicate contract id with same facts for score confidence and abstention semantics | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V28 | duplicate contract id with conflicting facts for score confidence and abstention semantics | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V29 | score framework rejects factor bundle for score confidence and abstention semantics | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V30 | score framework returns sealed score for score confidence and abstention semantics | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V31 | RiskProfile prefers no entry for score confidence and abstention semantics | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V32 | PortfolioSnapshot contains exposure for score confidence and abstention semantics | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V33 | HistoricalSeries is injected for score confidence and abstention semantics | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V34 | broker adapter is available in process for score confidence and abstention semantics | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V35 | environment contains credentials for score confidence and abstention semantics | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V36 | order manager exists in composition for score confidence and abstention semantics | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V37 | risk engine exists in composition for score confidence and abstention semantics | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V38 | trade decision exists downstream for score confidence and abstention semantics | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V39 | serialization round trip for score confidence and abstention semantics | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AS-V40 | schema version incompatible for score confidence and abstention semantics | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AS-ACCEPT-001:** All forty vectors pass without external calls.
**AS-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AS-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AT — Signal mapping catalog
The vectors below verify TradingSignal action and structure-hint mapping. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AT-V01 | nominal valid input for TradingSignal action and structure-hint mapping | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V02 | required field absent for TradingSignal action and structure-hint mapping | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V03 | non-finite numeric value for TradingSignal action and structure-hint mapping | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V04 | boundary value equal to minimum for TradingSignal action and structure-hint mapping | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V05 | value one quantum below minimum for TradingSignal action and structure-hint mapping | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V06 | value one quantum above maximum for TradingSignal action and structure-hint mapping | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V07 | timestamp exactly at start boundary for TradingSignal action and structure-hint mapping | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V08 | timestamp exactly at end boundary for TradingSignal action and structure-hint mapping | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V09 | two otherwise equal candidates for TradingSignal action and structure-hint mapping | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V10 | input collection reversed for TradingSignal action and structure-hint mapping | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V11 | unrelated metadata added for TradingSignal action and structure-hint mapping | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V12 | optional metadata absent for TradingSignal action and structure-hint mapping | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V13 | optional metadata malformed for TradingSignal action and structure-hint mapping | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V14 | immutable input reused for TradingSignal action and structure-hint mapping | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V15 | same input evaluated twice for TradingSignal action and structure-hint mapping | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V16 | same input evaluated concurrently for TradingSignal action and structure-hint mapping | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V17 | event sink raises exception for TradingSignal action and structure-hint mapping | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V18 | unknown enum supplied for TradingSignal action and structure-hint mapping | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V19 | unknown payload field supplied for TradingSignal action and structure-hint mapping | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V20 | unsupported underlying supplied for TradingSignal action and structure-hint mapping | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V21 | snapshot id conflicts with contract identity for TradingSignal action and structure-hint mapping | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V22 | selected contract token absent for TradingSignal action and structure-hint mapping | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V23 | symbol absent but immutable instrument id present for TradingSignal action and structure-hint mapping | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V24 | decimal precision exceeds display precision for TradingSignal action and structure-hint mapping | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V25 | negative zero submitted for TradingSignal action and structure-hint mapping | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V26 | large but finite option chain for TradingSignal action and structure-hint mapping | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V27 | duplicate contract id with same facts for TradingSignal action and structure-hint mapping | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V28 | duplicate contract id with conflicting facts for TradingSignal action and structure-hint mapping | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V29 | score framework rejects factor bundle for TradingSignal action and structure-hint mapping | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V30 | score framework returns sealed score for TradingSignal action and structure-hint mapping | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V31 | RiskProfile prefers no entry for TradingSignal action and structure-hint mapping | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V32 | PortfolioSnapshot contains exposure for TradingSignal action and structure-hint mapping | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V33 | HistoricalSeries is injected for TradingSignal action and structure-hint mapping | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V34 | broker adapter is available in process for TradingSignal action and structure-hint mapping | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V35 | environment contains credentials for TradingSignal action and structure-hint mapping | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V36 | order manager exists in composition for TradingSignal action and structure-hint mapping | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V37 | risk engine exists in composition for TradingSignal action and structure-hint mapping | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V38 | trade decision exists downstream for TradingSignal action and structure-hint mapping | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V39 | serialization round trip for TradingSignal action and structure-hint mapping | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AT-V40 | schema version incompatible for TradingSignal action and structure-hint mapping | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AT-ACCEPT-001:** All forty vectors pass without external calls.
**AT-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AT-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AU — Recommendation serialization catalog
The vectors below verify canonical JSON recommendation payloads. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AU-V01 | nominal valid input for canonical JSON recommendation payloads | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V02 | required field absent for canonical JSON recommendation payloads | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V03 | non-finite numeric value for canonical JSON recommendation payloads | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V04 | boundary value equal to minimum for canonical JSON recommendation payloads | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V05 | value one quantum below minimum for canonical JSON recommendation payloads | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V06 | value one quantum above maximum for canonical JSON recommendation payloads | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V07 | timestamp exactly at start boundary for canonical JSON recommendation payloads | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V08 | timestamp exactly at end boundary for canonical JSON recommendation payloads | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V09 | two otherwise equal candidates for canonical JSON recommendation payloads | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V10 | input collection reversed for canonical JSON recommendation payloads | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V11 | unrelated metadata added for canonical JSON recommendation payloads | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V12 | optional metadata absent for canonical JSON recommendation payloads | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V13 | optional metadata malformed for canonical JSON recommendation payloads | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V14 | immutable input reused for canonical JSON recommendation payloads | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V15 | same input evaluated twice for canonical JSON recommendation payloads | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V16 | same input evaluated concurrently for canonical JSON recommendation payloads | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V17 | event sink raises exception for canonical JSON recommendation payloads | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V18 | unknown enum supplied for canonical JSON recommendation payloads | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V19 | unknown payload field supplied for canonical JSON recommendation payloads | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V20 | unsupported underlying supplied for canonical JSON recommendation payloads | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V21 | snapshot id conflicts with contract identity for canonical JSON recommendation payloads | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V22 | selected contract token absent for canonical JSON recommendation payloads | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V23 | symbol absent but immutable instrument id present for canonical JSON recommendation payloads | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V24 | decimal precision exceeds display precision for canonical JSON recommendation payloads | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V25 | negative zero submitted for canonical JSON recommendation payloads | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V26 | large but finite option chain for canonical JSON recommendation payloads | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V27 | duplicate contract id with same facts for canonical JSON recommendation payloads | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V28 | duplicate contract id with conflicting facts for canonical JSON recommendation payloads | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V29 | score framework rejects factor bundle for canonical JSON recommendation payloads | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V30 | score framework returns sealed score for canonical JSON recommendation payloads | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V31 | RiskProfile prefers no entry for canonical JSON recommendation payloads | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V32 | PortfolioSnapshot contains exposure for canonical JSON recommendation payloads | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V33 | HistoricalSeries is injected for canonical JSON recommendation payloads | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V34 | broker adapter is available in process for canonical JSON recommendation payloads | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V35 | environment contains credentials for canonical JSON recommendation payloads | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V36 | order manager exists in composition for canonical JSON recommendation payloads | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V37 | risk engine exists in composition for canonical JSON recommendation payloads | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V38 | trade decision exists downstream for canonical JSON recommendation payloads | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V39 | serialization round trip for canonical JSON recommendation payloads | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AU-V40 | schema version incompatible for canonical JSON recommendation payloads | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AU-ACCEPT-001:** All forty vectors pass without external calls.
**AU-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AU-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AV — Deserialization rejection catalog
The vectors below verify versioned payload validation. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AV-V01 | nominal valid input for versioned payload validation | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V02 | required field absent for versioned payload validation | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V03 | non-finite numeric value for versioned payload validation | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V04 | boundary value equal to minimum for versioned payload validation | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V05 | value one quantum below minimum for versioned payload validation | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V06 | value one quantum above maximum for versioned payload validation | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V07 | timestamp exactly at start boundary for versioned payload validation | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V08 | timestamp exactly at end boundary for versioned payload validation | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V09 | two otherwise equal candidates for versioned payload validation | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V10 | input collection reversed for versioned payload validation | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V11 | unrelated metadata added for versioned payload validation | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V12 | optional metadata absent for versioned payload validation | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V13 | optional metadata malformed for versioned payload validation | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V14 | immutable input reused for versioned payload validation | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V15 | same input evaluated twice for versioned payload validation | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V16 | same input evaluated concurrently for versioned payload validation | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V17 | event sink raises exception for versioned payload validation | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V18 | unknown enum supplied for versioned payload validation | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V19 | unknown payload field supplied for versioned payload validation | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V20 | unsupported underlying supplied for versioned payload validation | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V21 | snapshot id conflicts with contract identity for versioned payload validation | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V22 | selected contract token absent for versioned payload validation | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V23 | symbol absent but immutable instrument id present for versioned payload validation | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V24 | decimal precision exceeds display precision for versioned payload validation | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V25 | negative zero submitted for versioned payload validation | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V26 | large but finite option chain for versioned payload validation | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V27 | duplicate contract id with same facts for versioned payload validation | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V28 | duplicate contract id with conflicting facts for versioned payload validation | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V29 | score framework rejects factor bundle for versioned payload validation | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V30 | score framework returns sealed score for versioned payload validation | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V31 | RiskProfile prefers no entry for versioned payload validation | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V32 | PortfolioSnapshot contains exposure for versioned payload validation | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V33 | HistoricalSeries is injected for versioned payload validation | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V34 | broker adapter is available in process for versioned payload validation | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V35 | environment contains credentials for versioned payload validation | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V36 | order manager exists in composition for versioned payload validation | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V37 | risk engine exists in composition for versioned payload validation | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V38 | trade decision exists downstream for versioned payload validation | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V39 | serialization round trip for versioned payload validation | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AV-V40 | schema version incompatible for versioned payload validation | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AV-ACCEPT-001:** All forty vectors pass without external calls.
**AV-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AV-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AW — Registry integration catalog
The vectors below verify strategy registration and identity metadata. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AW-V01 | nominal valid input for strategy registration and identity metadata | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V02 | required field absent for strategy registration and identity metadata | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V03 | non-finite numeric value for strategy registration and identity metadata | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V04 | boundary value equal to minimum for strategy registration and identity metadata | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V05 | value one quantum below minimum for strategy registration and identity metadata | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V06 | value one quantum above maximum for strategy registration and identity metadata | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V07 | timestamp exactly at start boundary for strategy registration and identity metadata | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V08 | timestamp exactly at end boundary for strategy registration and identity metadata | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V09 | two otherwise equal candidates for strategy registration and identity metadata | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V10 | input collection reversed for strategy registration and identity metadata | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V11 | unrelated metadata added for strategy registration and identity metadata | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V12 | optional metadata absent for strategy registration and identity metadata | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V13 | optional metadata malformed for strategy registration and identity metadata | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V14 | immutable input reused for strategy registration and identity metadata | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V15 | same input evaluated twice for strategy registration and identity metadata | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V16 | same input evaluated concurrently for strategy registration and identity metadata | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V17 | event sink raises exception for strategy registration and identity metadata | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V18 | unknown enum supplied for strategy registration and identity metadata | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V19 | unknown payload field supplied for strategy registration and identity metadata | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V20 | unsupported underlying supplied for strategy registration and identity metadata | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V21 | snapshot id conflicts with contract identity for strategy registration and identity metadata | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V22 | selected contract token absent for strategy registration and identity metadata | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V23 | symbol absent but immutable instrument id present for strategy registration and identity metadata | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V24 | decimal precision exceeds display precision for strategy registration and identity metadata | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V25 | negative zero submitted for strategy registration and identity metadata | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V26 | large but finite option chain for strategy registration and identity metadata | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V27 | duplicate contract id with same facts for strategy registration and identity metadata | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V28 | duplicate contract id with conflicting facts for strategy registration and identity metadata | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V29 | score framework rejects factor bundle for strategy registration and identity metadata | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V30 | score framework returns sealed score for strategy registration and identity metadata | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V31 | RiskProfile prefers no entry for strategy registration and identity metadata | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V32 | PortfolioSnapshot contains exposure for strategy registration and identity metadata | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V33 | HistoricalSeries is injected for strategy registration and identity metadata | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V34 | broker adapter is available in process for strategy registration and identity metadata | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V35 | environment contains credentials for strategy registration and identity metadata | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V36 | order manager exists in composition for strategy registration and identity metadata | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V37 | risk engine exists in composition for strategy registration and identity metadata | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V38 | trade decision exists downstream for strategy registration and identity metadata | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V39 | serialization round trip for strategy registration and identity metadata | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AW-V40 | schema version incompatible for strategy registration and identity metadata | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AW-ACCEPT-001:** All forty vectors pass without external calls.
**AW-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AW-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AX — Event isolation catalog
The vectors below verify observational event-sink behavior. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AX-V01 | nominal valid input for observational event-sink behavior | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V02 | required field absent for observational event-sink behavior | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V03 | non-finite numeric value for observational event-sink behavior | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V04 | boundary value equal to minimum for observational event-sink behavior | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V05 | value one quantum below minimum for observational event-sink behavior | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V06 | value one quantum above maximum for observational event-sink behavior | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V07 | timestamp exactly at start boundary for observational event-sink behavior | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V08 | timestamp exactly at end boundary for observational event-sink behavior | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V09 | two otherwise equal candidates for observational event-sink behavior | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V10 | input collection reversed for observational event-sink behavior | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V11 | unrelated metadata added for observational event-sink behavior | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V12 | optional metadata absent for observational event-sink behavior | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V13 | optional metadata malformed for observational event-sink behavior | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V14 | immutable input reused for observational event-sink behavior | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V15 | same input evaluated twice for observational event-sink behavior | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V16 | same input evaluated concurrently for observational event-sink behavior | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V17 | event sink raises exception for observational event-sink behavior | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V18 | unknown enum supplied for observational event-sink behavior | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V19 | unknown payload field supplied for observational event-sink behavior | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V20 | unsupported underlying supplied for observational event-sink behavior | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V21 | snapshot id conflicts with contract identity for observational event-sink behavior | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V22 | selected contract token absent for observational event-sink behavior | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V23 | symbol absent but immutable instrument id present for observational event-sink behavior | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V24 | decimal precision exceeds display precision for observational event-sink behavior | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V25 | negative zero submitted for observational event-sink behavior | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V26 | large but finite option chain for observational event-sink behavior | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V27 | duplicate contract id with same facts for observational event-sink behavior | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V28 | duplicate contract id with conflicting facts for observational event-sink behavior | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V29 | score framework rejects factor bundle for observational event-sink behavior | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V30 | score framework returns sealed score for observational event-sink behavior | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V31 | RiskProfile prefers no entry for observational event-sink behavior | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V32 | PortfolioSnapshot contains exposure for observational event-sink behavior | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V33 | HistoricalSeries is injected for observational event-sink behavior | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V34 | broker adapter is available in process for observational event-sink behavior | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V35 | environment contains credentials for observational event-sink behavior | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V36 | order manager exists in composition for observational event-sink behavior | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V37 | risk engine exists in composition for observational event-sink behavior | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V38 | trade decision exists downstream for observational event-sink behavior | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V39 | serialization round trip for observational event-sink behavior | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AX-V40 | schema version incompatible for observational event-sink behavior | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AX-ACCEPT-001:** All forty vectors pass without external calls.
**AX-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AX-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AY — Thread-safety catalog
The vectors below verify stateless concurrent evaluation behavior. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AY-V01 | nominal valid input for stateless concurrent evaluation behavior | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V02 | required field absent for stateless concurrent evaluation behavior | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V03 | non-finite numeric value for stateless concurrent evaluation behavior | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V04 | boundary value equal to minimum for stateless concurrent evaluation behavior | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V05 | value one quantum below minimum for stateless concurrent evaluation behavior | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V06 | value one quantum above maximum for stateless concurrent evaluation behavior | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V07 | timestamp exactly at start boundary for stateless concurrent evaluation behavior | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V08 | timestamp exactly at end boundary for stateless concurrent evaluation behavior | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V09 | two otherwise equal candidates for stateless concurrent evaluation behavior | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V10 | input collection reversed for stateless concurrent evaluation behavior | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V11 | unrelated metadata added for stateless concurrent evaluation behavior | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V12 | optional metadata absent for stateless concurrent evaluation behavior | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V13 | optional metadata malformed for stateless concurrent evaluation behavior | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V14 | immutable input reused for stateless concurrent evaluation behavior | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V15 | same input evaluated twice for stateless concurrent evaluation behavior | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V16 | same input evaluated concurrently for stateless concurrent evaluation behavior | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V17 | event sink raises exception for stateless concurrent evaluation behavior | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V18 | unknown enum supplied for stateless concurrent evaluation behavior | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V19 | unknown payload field supplied for stateless concurrent evaluation behavior | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V20 | unsupported underlying supplied for stateless concurrent evaluation behavior | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V21 | snapshot id conflicts with contract identity for stateless concurrent evaluation behavior | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V22 | selected contract token absent for stateless concurrent evaluation behavior | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V23 | symbol absent but immutable instrument id present for stateless concurrent evaluation behavior | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V24 | decimal precision exceeds display precision for stateless concurrent evaluation behavior | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V25 | negative zero submitted for stateless concurrent evaluation behavior | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V26 | large but finite option chain for stateless concurrent evaluation behavior | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V27 | duplicate contract id with same facts for stateless concurrent evaluation behavior | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V28 | duplicate contract id with conflicting facts for stateless concurrent evaluation behavior | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V29 | score framework rejects factor bundle for stateless concurrent evaluation behavior | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V30 | score framework returns sealed score for stateless concurrent evaluation behavior | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V31 | RiskProfile prefers no entry for stateless concurrent evaluation behavior | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V32 | PortfolioSnapshot contains exposure for stateless concurrent evaluation behavior | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V33 | HistoricalSeries is injected for stateless concurrent evaluation behavior | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V34 | broker adapter is available in process for stateless concurrent evaluation behavior | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V35 | environment contains credentials for stateless concurrent evaluation behavior | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V36 | order manager exists in composition for stateless concurrent evaluation behavior | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V37 | risk engine exists in composition for stateless concurrent evaluation behavior | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V38 | trade decision exists downstream for stateless concurrent evaluation behavior | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V39 | serialization round trip for stateless concurrent evaluation behavior | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AY-V40 | schema version incompatible for stateless concurrent evaluation behavior | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AY-ACCEPT-001:** All forty vectors pass without external calls.
**AY-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AY-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix AZ — Determinism catalog
The vectors below verify stable arithmetic, ordering, and identifiers. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| AZ-V01 | nominal valid input for stable arithmetic, ordering, and identifiers | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V02 | required field absent for stable arithmetic, ordering, and identifiers | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V03 | non-finite numeric value for stable arithmetic, ordering, and identifiers | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V04 | boundary value equal to minimum for stable arithmetic, ordering, and identifiers | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V05 | value one quantum below minimum for stable arithmetic, ordering, and identifiers | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V06 | value one quantum above maximum for stable arithmetic, ordering, and identifiers | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V07 | timestamp exactly at start boundary for stable arithmetic, ordering, and identifiers | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V08 | timestamp exactly at end boundary for stable arithmetic, ordering, and identifiers | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V09 | two otherwise equal candidates for stable arithmetic, ordering, and identifiers | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V10 | input collection reversed for stable arithmetic, ordering, and identifiers | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V11 | unrelated metadata added for stable arithmetic, ordering, and identifiers | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V12 | optional metadata absent for stable arithmetic, ordering, and identifiers | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V13 | optional metadata malformed for stable arithmetic, ordering, and identifiers | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V14 | immutable input reused for stable arithmetic, ordering, and identifiers | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V15 | same input evaluated twice for stable arithmetic, ordering, and identifiers | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V16 | same input evaluated concurrently for stable arithmetic, ordering, and identifiers | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V17 | event sink raises exception for stable arithmetic, ordering, and identifiers | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V18 | unknown enum supplied for stable arithmetic, ordering, and identifiers | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V19 | unknown payload field supplied for stable arithmetic, ordering, and identifiers | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V20 | unsupported underlying supplied for stable arithmetic, ordering, and identifiers | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V21 | snapshot id conflicts with contract identity for stable arithmetic, ordering, and identifiers | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V22 | selected contract token absent for stable arithmetic, ordering, and identifiers | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V23 | symbol absent but immutable instrument id present for stable arithmetic, ordering, and identifiers | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V24 | decimal precision exceeds display precision for stable arithmetic, ordering, and identifiers | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V25 | negative zero submitted for stable arithmetic, ordering, and identifiers | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V26 | large but finite option chain for stable arithmetic, ordering, and identifiers | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V27 | duplicate contract id with same facts for stable arithmetic, ordering, and identifiers | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V28 | duplicate contract id with conflicting facts for stable arithmetic, ordering, and identifiers | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V29 | score framework rejects factor bundle for stable arithmetic, ordering, and identifiers | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V30 | score framework returns sealed score for stable arithmetic, ordering, and identifiers | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V31 | RiskProfile prefers no entry for stable arithmetic, ordering, and identifiers | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V32 | PortfolioSnapshot contains exposure for stable arithmetic, ordering, and identifiers | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V33 | HistoricalSeries is injected for stable arithmetic, ordering, and identifiers | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V34 | broker adapter is available in process for stable arithmetic, ordering, and identifiers | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V35 | environment contains credentials for stable arithmetic, ordering, and identifiers | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V36 | order manager exists in composition for stable arithmetic, ordering, and identifiers | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V37 | risk engine exists in composition for stable arithmetic, ordering, and identifiers | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V38 | trade decision exists downstream for stable arithmetic, ordering, and identifiers | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V39 | serialization round trip for stable arithmetic, ordering, and identifiers | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| AZ-V40 | schema version incompatible for stable arithmetic, ordering, and identifiers | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**AZ-ACCEPT-001:** All forty vectors pass without external calls.
**AZ-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**AZ-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix BA — Boundary enforcement catalog
The vectors below verify forbidden broker, risk, and execution behavior. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| BA-V01 | nominal valid input for forbidden broker, risk, and execution behavior | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V02 | required field absent for forbidden broker, risk, and execution behavior | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V03 | non-finite numeric value for forbidden broker, risk, and execution behavior | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V04 | boundary value equal to minimum for forbidden broker, risk, and execution behavior | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V05 | value one quantum below minimum for forbidden broker, risk, and execution behavior | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V06 | value one quantum above maximum for forbidden broker, risk, and execution behavior | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V07 | timestamp exactly at start boundary for forbidden broker, risk, and execution behavior | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V08 | timestamp exactly at end boundary for forbidden broker, risk, and execution behavior | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V09 | two otherwise equal candidates for forbidden broker, risk, and execution behavior | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V10 | input collection reversed for forbidden broker, risk, and execution behavior | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V11 | unrelated metadata added for forbidden broker, risk, and execution behavior | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V12 | optional metadata absent for forbidden broker, risk, and execution behavior | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V13 | optional metadata malformed for forbidden broker, risk, and execution behavior | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V14 | immutable input reused for forbidden broker, risk, and execution behavior | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V15 | same input evaluated twice for forbidden broker, risk, and execution behavior | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V16 | same input evaluated concurrently for forbidden broker, risk, and execution behavior | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V17 | event sink raises exception for forbidden broker, risk, and execution behavior | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V18 | unknown enum supplied for forbidden broker, risk, and execution behavior | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V19 | unknown payload field supplied for forbidden broker, risk, and execution behavior | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V20 | unsupported underlying supplied for forbidden broker, risk, and execution behavior | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V21 | snapshot id conflicts with contract identity for forbidden broker, risk, and execution behavior | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V22 | selected contract token absent for forbidden broker, risk, and execution behavior | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V23 | symbol absent but immutable instrument id present for forbidden broker, risk, and execution behavior | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V24 | decimal precision exceeds display precision for forbidden broker, risk, and execution behavior | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V25 | negative zero submitted for forbidden broker, risk, and execution behavior | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V26 | large but finite option chain for forbidden broker, risk, and execution behavior | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V27 | duplicate contract id with same facts for forbidden broker, risk, and execution behavior | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V28 | duplicate contract id with conflicting facts for forbidden broker, risk, and execution behavior | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V29 | score framework rejects factor bundle for forbidden broker, risk, and execution behavior | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V30 | score framework returns sealed score for forbidden broker, risk, and execution behavior | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V31 | RiskProfile prefers no entry for forbidden broker, risk, and execution behavior | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V32 | PortfolioSnapshot contains exposure for forbidden broker, risk, and execution behavior | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V33 | HistoricalSeries is injected for forbidden broker, risk, and execution behavior | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V34 | broker adapter is available in process for forbidden broker, risk, and execution behavior | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V35 | environment contains credentials for forbidden broker, risk, and execution behavior | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V36 | order manager exists in composition for forbidden broker, risk, and execution behavior | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V37 | risk engine exists in composition for forbidden broker, risk, and execution behavior | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V38 | trade decision exists downstream for forbidden broker, risk, and execution behavior | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V39 | serialization round trip for forbidden broker, risk, and execution behavior | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BA-V40 | schema version incompatible for forbidden broker, risk, and execution behavior | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**BA-ACCEPT-001:** All forty vectors pass without external calls.
**BA-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**BA-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix BB — Portfolio-hint catalog
The vectors below verify read-only portfolio and risk-profile hints. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| BB-V01 | nominal valid input for read-only portfolio and risk-profile hints | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V02 | required field absent for read-only portfolio and risk-profile hints | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V03 | non-finite numeric value for read-only portfolio and risk-profile hints | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V04 | boundary value equal to minimum for read-only portfolio and risk-profile hints | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V05 | value one quantum below minimum for read-only portfolio and risk-profile hints | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V06 | value one quantum above maximum for read-only portfolio and risk-profile hints | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V07 | timestamp exactly at start boundary for read-only portfolio and risk-profile hints | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V08 | timestamp exactly at end boundary for read-only portfolio and risk-profile hints | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V09 | two otherwise equal candidates for read-only portfolio and risk-profile hints | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V10 | input collection reversed for read-only portfolio and risk-profile hints | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V11 | unrelated metadata added for read-only portfolio and risk-profile hints | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V12 | optional metadata absent for read-only portfolio and risk-profile hints | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V13 | optional metadata malformed for read-only portfolio and risk-profile hints | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V14 | immutable input reused for read-only portfolio and risk-profile hints | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V15 | same input evaluated twice for read-only portfolio and risk-profile hints | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V16 | same input evaluated concurrently for read-only portfolio and risk-profile hints | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V17 | event sink raises exception for read-only portfolio and risk-profile hints | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V18 | unknown enum supplied for read-only portfolio and risk-profile hints | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V19 | unknown payload field supplied for read-only portfolio and risk-profile hints | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V20 | unsupported underlying supplied for read-only portfolio and risk-profile hints | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V21 | snapshot id conflicts with contract identity for read-only portfolio and risk-profile hints | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V22 | selected contract token absent for read-only portfolio and risk-profile hints | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V23 | symbol absent but immutable instrument id present for read-only portfolio and risk-profile hints | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V24 | decimal precision exceeds display precision for read-only portfolio and risk-profile hints | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V25 | negative zero submitted for read-only portfolio and risk-profile hints | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V26 | large but finite option chain for read-only portfolio and risk-profile hints | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V27 | duplicate contract id with same facts for read-only portfolio and risk-profile hints | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V28 | duplicate contract id with conflicting facts for read-only portfolio and risk-profile hints | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V29 | score framework rejects factor bundle for read-only portfolio and risk-profile hints | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V30 | score framework returns sealed score for read-only portfolio and risk-profile hints | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V31 | RiskProfile prefers no entry for read-only portfolio and risk-profile hints | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V32 | PortfolioSnapshot contains exposure for read-only portfolio and risk-profile hints | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V33 | HistoricalSeries is injected for read-only portfolio and risk-profile hints | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V34 | broker adapter is available in process for read-only portfolio and risk-profile hints | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V35 | environment contains credentials for read-only portfolio and risk-profile hints | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V36 | order manager exists in composition for read-only portfolio and risk-profile hints | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V37 | risk engine exists in composition for read-only portfolio and risk-profile hints | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V38 | trade decision exists downstream for read-only portfolio and risk-profile hints | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V39 | serialization round trip for read-only portfolio and risk-profile hints | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BB-V40 | schema version incompatible for read-only portfolio and risk-profile hints | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**BB-ACCEPT-001:** All forty vectors pass without external calls.
**BB-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**BB-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix BC — Historical-context catalog
The vectors below verify injected historical-series fallback behavior. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| BC-V01 | nominal valid input for injected historical-series fallback behavior | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V02 | required field absent for injected historical-series fallback behavior | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V03 | non-finite numeric value for injected historical-series fallback behavior | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V04 | boundary value equal to minimum for injected historical-series fallback behavior | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V05 | value one quantum below minimum for injected historical-series fallback behavior | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V06 | value one quantum above maximum for injected historical-series fallback behavior | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V07 | timestamp exactly at start boundary for injected historical-series fallback behavior | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V08 | timestamp exactly at end boundary for injected historical-series fallback behavior | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V09 | two otherwise equal candidates for injected historical-series fallback behavior | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V10 | input collection reversed for injected historical-series fallback behavior | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V11 | unrelated metadata added for injected historical-series fallback behavior | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V12 | optional metadata absent for injected historical-series fallback behavior | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V13 | optional metadata malformed for injected historical-series fallback behavior | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V14 | immutable input reused for injected historical-series fallback behavior | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V15 | same input evaluated twice for injected historical-series fallback behavior | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V16 | same input evaluated concurrently for injected historical-series fallback behavior | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V17 | event sink raises exception for injected historical-series fallback behavior | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V18 | unknown enum supplied for injected historical-series fallback behavior | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V19 | unknown payload field supplied for injected historical-series fallback behavior | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V20 | unsupported underlying supplied for injected historical-series fallback behavior | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V21 | snapshot id conflicts with contract identity for injected historical-series fallback behavior | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V22 | selected contract token absent for injected historical-series fallback behavior | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V23 | symbol absent but immutable instrument id present for injected historical-series fallback behavior | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V24 | decimal precision exceeds display precision for injected historical-series fallback behavior | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V25 | negative zero submitted for injected historical-series fallback behavior | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V26 | large but finite option chain for injected historical-series fallback behavior | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V27 | duplicate contract id with same facts for injected historical-series fallback behavior | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V28 | duplicate contract id with conflicting facts for injected historical-series fallback behavior | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V29 | score framework rejects factor bundle for injected historical-series fallback behavior | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V30 | score framework returns sealed score for injected historical-series fallback behavior | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V31 | RiskProfile prefers no entry for injected historical-series fallback behavior | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V32 | PortfolioSnapshot contains exposure for injected historical-series fallback behavior | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V33 | HistoricalSeries is injected for injected historical-series fallback behavior | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V34 | broker adapter is available in process for injected historical-series fallback behavior | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V35 | environment contains credentials for injected historical-series fallback behavior | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V36 | order manager exists in composition for injected historical-series fallback behavior | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V37 | risk engine exists in composition for injected historical-series fallback behavior | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V38 | trade decision exists downstream for injected historical-series fallback behavior | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V39 | serialization round trip for injected historical-series fallback behavior | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BC-V40 | schema version incompatible for injected historical-series fallback behavior | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**BC-ACCEPT-001:** All forty vectors pass without external calls.
**BC-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**BC-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.

## Appendix BD — Performance catalog
The vectors below verify complexity and benchmark acceptance vectors. A test fixture injects all facts and must assert no broker, network, environment, order-manager, Risk Engine, or mutable-input interaction.
| Vector | Fixture perturbation | Required assertion |
|---|---|---|
| BD-V01 | nominal valid input for complexity and benchmark acceptance vectors | expected pass and sealed evidence; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V02 | required field absent for complexity and benchmark acceptance vectors | expected reject with the documented SSS code; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V03 | non-finite numeric value for complexity and benchmark acceptance vectors | expected reject before scoring; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V04 | boundary value equal to minimum for complexity and benchmark acceptance vectors | expected inclusive pass where specified; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V05 | value one quantum below minimum for complexity and benchmark acceptance vectors | expected abstain where the condition is suitability; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V06 | value one quantum above maximum for complexity and benchmark acceptance vectors | expected abstain or reject under the named rule; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V07 | timestamp exactly at start boundary for complexity and benchmark acceptance vectors | expected entry-window inclusion; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V08 | timestamp exactly at end boundary for complexity and benchmark acceptance vectors | expected entry-window abstention; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V09 | two otherwise equal candidates for complexity and benchmark acceptance vectors | expected documented lexical tie-break; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V10 | input collection reversed for complexity and benchmark acceptance vectors | expected byte-equivalent result; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V11 | unrelated metadata added for complexity and benchmark acceptance vectors | expected unchanged selection and score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V12 | optional metadata absent for complexity and benchmark acceptance vectors | expected documented fallback or no-op; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V13 | optional metadata malformed for complexity and benchmark acceptance vectors | expected safe reject without external access; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V14 | immutable input reused for complexity and benchmark acceptance vectors | expected no mutation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V15 | same input evaluated twice for complexity and benchmark acceptance vectors | expected identical canonical JSON; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V16 | same input evaluated concurrently for complexity and benchmark acceptance vectors | expected isolated immutable results; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V17 | event sink raises exception for complexity and benchmark acceptance vectors | expected sealed result and isolated sink failure; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V18 | unknown enum supplied for complexity and benchmark acceptance vectors | expected reject during validation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V19 | unknown payload field supplied for complexity and benchmark acceptance vectors | expected reader-policy behavior; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V20 | unsupported underlying supplied for complexity and benchmark acceptance vectors | expected explicit rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V21 | snapshot id conflicts with contract identity for complexity and benchmark acceptance vectors | expected reject before selection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V22 | selected contract token absent for complexity and benchmark acceptance vectors | expected valid recommendation with null token; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V23 | symbol absent but immutable instrument id present for complexity and benchmark acceptance vectors | expected valid recommendation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V24 | decimal precision exceeds display precision for complexity and benchmark acceptance vectors | expected deterministic sealing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V25 | negative zero submitted for complexity and benchmark acceptance vectors | expected normalization or strict rejection by field contract; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V26 | large but finite option chain for complexity and benchmark acceptance vectors | expected bounded deterministic processing; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V27 | duplicate contract id with same facts for complexity and benchmark acceptance vectors | expected deterministic deduplication policy; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V28 | duplicate contract id with conflicting facts for complexity and benchmark acceptance vectors | expected chain rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V29 | score framework rejects factor bundle for complexity and benchmark acceptance vectors | expected SSS.SCORING.FAILED reject; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V30 | score framework returns sealed score for complexity and benchmark acceptance vectors | expected embedded immutable score; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V31 | RiskProfile prefers no entry for complexity and benchmark acceptance vectors | expected informational preservation, not enforcement; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V32 | PortfolioSnapshot contains exposure for complexity and benchmark acceptance vectors | expected no portfolio mutation or calculation; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V33 | HistoricalSeries is injected for complexity and benchmark acceptance vectors | expected no data fetch and explicit provenance; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V34 | broker adapter is available in process for complexity and benchmark acceptance vectors | expected strategy not to call it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V35 | environment contains credentials for complexity and benchmark acceptance vectors | expected strategy not to read them; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V36 | order manager exists in composition for complexity and benchmark acceptance vectors | expected strategy not to reference it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V37 | risk engine exists in composition for complexity and benchmark acceptance vectors | expected strategy not to invoke it; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V38 | trade decision exists downstream for complexity and benchmark acceptance vectors | expected strategy not to self-select; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V39 | serialization round trip for complexity and benchmark acceptance vectors | expected invariant-preserving reconstructed model; preserve BOUNDARY-SSS and deterministic audit reasons. |
| BD-V40 | schema version incompatible for complexity and benchmark acceptance vectors | expected deserialization rejection; preserve BOUNDARY-SSS and deterministic audit reasons. |

**BD-ACCEPT-001:** All forty vectors pass without external calls.
**BD-ACCEPT-002:** Every failure result is immutable, serializable, and reason-coded.
**BD-ACCEPT-003:** An `ENTER` result, if produced, retains `UNDEFINED_UNLIMITED` max-loss semantics.
