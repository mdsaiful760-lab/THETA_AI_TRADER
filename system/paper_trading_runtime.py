"""Paper trading runtime wiring for THETA AI TRADER.

Pure composition over already-implemented, unmodified engines. Reuses
:class:`decision.ai_decision_loop.AIDecisionLoop`,
:class:`option_contract_selector.OptionContractSelector`,
:class:`execution.execution_engine.ExecutionEngine`,
:class:`paper_trading.paper_trading_runner.PaperTradingRunner`, and
:class:`system.system_orchestrator.SystemOrchestrator` exactly as designed —
this module reimplements no market-data, OI, regime, strategy, risk,
position-sizing, contract selection, or execution planning logic.

Full pipeline wired end to end::

    live MarketSnapshot (+ OI, regime — inside AIDecisionLoop)
        -> AIDecisionLoop.run_once()                     (strategy -> risk -> sizing)
        -> PaperTradeDecision                             (decision callback)
        -> OptionContractSelector.select_contract()        (existing selector, one call per leg)
        -> ContractSelectionResult                          (execution_engine's own input model)
        -> PositionSizingHint                               (repackaged from the decision's own numbers)
        -> ExecutionEngine.plan_from_run_context()           (existing ExecutionPlan model, READY)
        -> PaperTradingRunner.simulate_plan()                 (paper fill / position / P&L)

Contract selection: ``PaperTradeDecision`` carries a
:class:`decision.trade_decision_engine.TradeDecisionResult`-derived
``TradingSignal`` (via ``risk_decision.trading_signal``) whose
``structure_hint`` already states, per already-selected strategy, the real
leg count and each leg's option type (every currently implemented strategy —
short strangle, iron condor, bull put spread, bear call spread — populates
this). For each leg this module calls the existing, unmodified
``OptionContractSelector.select_contract`` with the real option chain from
the same live snapshot the decision was made against; it never fabricates a
strike, delta, or instrument. When any leg cannot be resolved (no structure
hint, or the selector finds no eligible contract), no ``ContractSelectionResult``
is built and the plan is skipped exactly as ``ExecutionEngine`` already gates
it (``"Contract selection required in LIVE mode."``) — never a bypass.

Position sizing: ``AIDecisionLoop`` already runs the real
``position_sizing_engine.PositionSizingEngine`` and stores its output on
``PaperTradeDecision`` as ``final_lots``/``final_quantity`` (plus the risk
engine's ``approved_risk_budget``/``approved_risk_pct``) — but does not
package a ``PositionSizingHint`` object. This module repackages those
already-computed numbers into one (per-leg, via ``leg_{n}_quantity``
metadata, so a 2-leg strangle is not accidentally halved by
``ExecutionEngine``'s even-split fallback) so ``ExecutionEngine``'s existing
"Position sizing hint required in LIVE mode." gate is satisfied with real
data — never a fabricated size.

``SystemOrchestrator``'s :class:`~core.event_bus.EventBus` is shared with
``PaperTradingRunner`` so paper-trading lifecycle events
(``paper.order.plan.completed`` etc.) flow onto the same bus instance the
orchestrator owns. ``SystemOrchestrator.start()``/``.stop()`` are
deliberately NOT invoked here: that lifecycle initializes a *different*
registry of engines (its own strategy/risk/execution/order/position/
portfolio slots for the orchestrator's own pre-trade/post-fill pipeline),
which this runtime does not populate — calling it would only report a
misleading FAILED/DEGRADED startup rather than reflect this runtime's
actual health. The orchestrator instance remains available on
``PaperTradingRuntime.orchestrator`` for a caller who separately wires that
registry.

The live :class:`PaperTradingRunner` is registered with the dashboard's
existing live-handle registry (``dashboard.live_session_adapter``) so the
Orders, Positions, Portfolio, and Paper Trading pages pick it up
automatically — no dashboard page is modified.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace
from typing import Protocol

from core.event_bus import EventBus
from dashboard.live_session_adapter import (
    DashboardLiveHandles,
    clear_live_handles,
    register_live_handles,
)
from decision.ai_decision_loop import (
    AIDecisionLoop,
    PaperTradeDecision,
    PaperTradeDecisionStatus,
)
from execution.execution_engine import (
    ContractSelectionResult,
    ExecutionEngine,
    ExecutionPlan,
    ExecutionPlanStatus,
    ExecutionRunContext,
    PositionSizingHint,
    SelectedContractLeg,
    default_execution_engine_config,
)
from market_data.market_snapshot import MarketSnapshot, OptionContractSnapshot, OptionType
from option_contract_selector import OptionContractSelector
from paper_trading.paper_trading_runner import (
    PaperExecutionResult,
    PaperSimulationContext,
    PaperTradingRunner,
    PaperTradingRunnerConfig,
    default_paper_trading_runner_config,
)
from system.system_orchestrator import SystemOrchestrator

_LOGGER = logging.getLogger("system.paper_trading_runtime")

# Traceability-only value passed to OptionContractSelector.select_contract's
# `side` parameter. Per its own docstring ("Side is retained for strategy
# traceability. It does NOT change risk authority.") and its scoring/filter
# code (keyed only on abs_delta/liquidity/spread), `side` never influences
# which contract is chosen, and neither `SelectedContractLeg` nor
# `ContractSelectionResult` has a side field to carry it further downstream.
# Every strategy family currently implemented in this codebase (short
# strangle, iron condor, bull put spread, bear call spread) is a
# premium-selling entry structure, so "SELL" is a real, non-arbitrary
# description of today's strategies rather than a fabricated placeholder.
_CONTRACT_SELECTION_SIDE = "SELL"


def _utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


class MarketDataSnapshotSource(Protocol):
    """Structural protocol matching ``decision.ai_decision_loop.SnapshotProvider``."""

    def get_snapshot(self, underlying: str) -> MarketSnapshot | None:
        """Return the latest cached snapshot for ``underlying``, or ``None``."""


def _marks_from_plan(plan: ExecutionPlan) -> dict[str, Decimal]:
    """Build a paper mark-price map from an execution plan's leg hints.

    Mirrors the ``marks_from_plan`` convention already established in
    ``tests/test_paper_trading_runner.py`` — this runtime has no live
    fill-price feed of its own, so it uses each leg's planned limit-price
    hint (produced upstream by contract selection / execution planning) as
    the paper mark, falling back to a fixed placeholder mark only when a
    leg carries no hint at all.
    """
    marks: dict[str, Decimal] = {}
    for leg in plan.legs:
        if leg.limit_price_hint is not None:
            marks[leg.instrument_key] = Decimal(str(leg.limit_price_hint))
        else:
            marks[leg.instrument_key] = Decimal("100.00")
    return marks


def _contract_snapshot_to_candidate(contract: OptionContractSnapshot) -> dict[str, object]:
    """Map one real ``OptionContractSnapshot`` onto the plain-dict shape
    ``option_contract_selector.OptionContractSelector.select_contract`` expects.

    Field-by-field forwarding only — every value already exists on the live
    snapshot; nothing here is derived, estimated, or fabricated.
    """
    return {
        "underlying": contract.underlying,
        "exchange": contract.exchange,
        "tradingsymbol": contract.tradingsymbol,
        "expiry": contract.expiry,
        "strike": contract.strike,
        "option_type": contract.option_type.value,
        "lot_size": contract.lot_size,
        "ltp": contract.ltp,
        "bid": contract.bid,
        "ask": contract.ask,
        "volume": contract.volume,
        "open_interest": contract.open_interest,
        "delta": contract.delta,
        "iv": contract.iv,
        "instrument_token": contract.instrument_token,
        "exchange_token": contract.exchange_token,
        "tick_size": contract.tick_size,
    }


def _parse_expiry_date(value: object) -> date:
    """Parse a snapshot/signal expiry (``str``, ``date``, or ``datetime``) into a ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _contract_selection_fingerprint(
    *, correlation_id: str, underlying: str, legs: tuple[SelectedContractLeg, ...]
) -> str:
    """Deterministic audit fingerprint, matching this codebase's canonical-JSON + SHA-256 convention."""
    payload = {
        "correlation_id": correlation_id,
        "underlying": underlying,
        "legs": [
            {
                "leg_index": leg.leg_index,
                "instrument_key": leg.instrument_key,
                "strike": leg.strike,
                "option_type": leg.option_type.value if leg.option_type is not None else None,
            }
            for leg in legs
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PaperTradeExecutionOutcome:
    """Audit record of one decision -> plan -> paper-fill attempt.

    Not a new domain model — a thin, read-only wrapper around already-sealed
    outputs (``PaperTradeDecision``, ``ExecutionPlan``,
    ``PaperExecutionResult``) so callers/tests can observe what this
    runtime's callback did for one decision without re-deriving it.
    """

    decision: PaperTradeDecision
    execution_plan: ExecutionPlan | None
    paper_result: PaperExecutionResult | None
    skipped_reason: str | None = None


class PaperTradingRuntime:
    """Wires ``AIDecisionLoop`` -> ``ExecutionEngine`` -> ``PaperTradingRunner``.

    Every ``TRADE_CANDIDATE`` decision produced by ``decision_loop`` is
    automatically converted into an ``ExecutionPlan`` (via
    ``ExecutionEngine.plan_from_run_context``) and, when that plan is
    ``READY``, simulated into a paper fill (via
    ``PaperTradingRunner.simulate_plan``) — no manual step required once
    :meth:`start` has been called.
    """

    def __init__(
        self,
        *,
        decision_loop: AIDecisionLoop,
        market_data_engine: MarketDataSnapshotSource,
        orchestrator: SystemOrchestrator | None = None,
        contract_selector: OptionContractSelector | None = None,
        execution_engine: ExecutionEngine | None = None,
        paper_trading_runner: PaperTradingRunner | None = None,
        paper_trading_runner_config: PaperTradingRunnerConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        on_outcome: Callable[[PaperTradeExecutionOutcome], None] | None = None,
    ) -> None:
        """Compose the paper-trading runtime from already-constructed engines.

        Args:
            decision_loop: Already-constructed ``AIDecisionLoop`` (its own
                ``market_data_engine`` argument must be the same object
                passed here as ``market_data_engine``, so this runtime can
                re-resolve the snapshot a decision was made against).
            market_data_engine: The snapshot source injected into
                ``decision_loop`` — used to fetch the ``MarketSnapshot`` an
                ``ExecutionRunContext`` requires (``PaperTradeDecision``
                does not itself carry the snapshot).
            orchestrator: Optional ``SystemOrchestrator`` to share an
                ``EventBus`` with; a fresh one is constructed when omitted.
            contract_selector: Optional ``OptionContractSelector``; a fresh
                one with default (``BALANCED``) profile is constructed when
                omitted. Never re-implemented — this runtime only calls its
                existing ``select_contract`` method.
            execution_engine: Optional ``ExecutionEngine``; a fresh one
                with default configuration is constructed when omitted.
            paper_trading_runner: Optional pre-built ``PaperTradingRunner``;
                when omitted, one is constructed bound to the shared
                ``EventBus`` from ``orchestrator``.
            paper_trading_runner_config: Config used only when
                ``paper_trading_runner`` is omitted.
            clock: Injectable clock for deterministic tests.
            on_outcome: Optional observer invoked with every decision
                outcome (trade candidates and non-trade decisions alike),
                for audit logging or tests.
        """
        self._decision_loop = decision_loop
        self._market_data_engine = market_data_engine
        self._clock = clock or _utc_now
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._on_outcome = on_outcome

        self.orchestrator = orchestrator or SystemOrchestrator()
        self.contract_selector = contract_selector or OptionContractSelector()
        self.execution_engine = execution_engine or ExecutionEngine(
            default_execution_engine_config()
        )
        self.paper_trading_runner = paper_trading_runner or PaperTradingRunner(
            paper_trading_runner_config or default_paper_trading_runner_config(),
            self.orchestrator.event_bus,
        )

        self._latest_outcome: PaperTradeExecutionOutcome | None = None
        self._decision_loop.add_decision_callback(self._on_decision)

    @property
    def event_bus(self) -> EventBus:
        """Return the event bus shared between the orchestrator and paper runner."""
        return self.orchestrator.event_bus

    def start(self, *, interval_seconds: float | None = None) -> None:
        """Register live dashboard handles and start the background decision loop."""
        self._register_dashboard_handles()
        self._decision_loop.start_loop(interval_seconds=interval_seconds)

    def stop(self) -> None:
        """Stop the background decision loop and clear live dashboard handles."""
        self._decision_loop.shutdown()
        clear_live_handles()

    def run_once(self) -> PaperTradeExecutionOutcome:
        """Run exactly one decision cycle synchronously and return its outcome.

        Useful for tests and manual/CLI runs; the background loop started
        by :meth:`start` calls the same path via the decision callback.
        """
        decision = self._decision_loop.run_once()
        outcome = self._latest_outcome
        if outcome is not None and outcome.decision.decision_id == decision.decision_id:
            return outcome
        return PaperTradeExecutionOutcome(
            decision=decision, execution_plan=None, paper_result=None, skipped_reason=None
        )

    def _register_dashboard_handles(self) -> None:
        """Register this runtime's live paper runner/engines with the dashboard."""
        register_live_handles(
            DashboardLiveHandles(
                market_streaming=self._market_data_engine,
                paper_runner=self.paper_trading_runner,
                evaluation_bundle_provider=self._decision_loop.get_latest_bundle,
                market_regime_provider=self._latest_regime,
            )
        )

    def _latest_regime(self) -> object | None:
        """Adapt the loop's latest decision into the dashboard's regime shape."""
        decision = self._decision_loop.get_latest_decision()
        if decision is None:
            return None
        return SimpleNamespace(regime=decision.market_regime, market_regime=decision.market_regime)

    def _on_decision(self, decision: PaperTradeDecision) -> None:
        """Decision-loop callback: build an ExecutionPlan and simulate a paper fill."""
        outcome = self._execute_decision(decision)
        self._latest_outcome = outcome
        if self._on_outcome is not None:
            try:
                self._on_outcome(outcome)
            except Exception:  # noqa: BLE001 - one bad observer must not break the loop
                _LOGGER.exception("paper_trading_runtime.on_outcome.failed")

    def _select_contracts(
        self, decision: PaperTradeDecision, snapshot: MarketSnapshot
    ) -> ContractSelectionResult | None:
        """Resolve one real contract per leg via the existing OptionContractSelector.

        Returns ``None`` (never a fabricated selection) when the upstream
        signal carries no structure hint, or when the selector cannot find
        an eligible contract for any leg — in both cases the caller passes
        ``contract_selection=None`` through unchanged, and
        ``ExecutionEngine`` applies its own existing, unmodified
        "Contract selection required in LIVE mode." gate exactly as before.
        """
        risk_decision = decision.risk_decision
        if risk_decision is None:
            return None
        signal = risk_decision.trading_signal
        structure = signal.structure_hint
        if structure is None or not structure.option_types:
            _LOGGER.info(
                "paper_trading_runtime.contract_selection.no_structure_hint",
                extra={"decision_id": decision.decision_id, "strategy_id": signal.strategy_id},
            )
            return None
        if len(structure.option_types) < structure.leg_count:
            _LOGGER.warning(
                "paper_trading_runtime.contract_selection.option_types_leg_count_mismatch",
                extra={"decision_id": decision.decision_id, "strategy_id": signal.strategy_id},
            )
            return None

        expiry_value = signal.market.expiry or snapshot.option_chain.metadata.expiry
        candidates = [
            _contract_snapshot_to_candidate(contract) for contract in snapshot.option_chain.contracts
        ]
        custom_config = (
            {"target_abs_delta": structure.target_delta} if structure.target_delta else None
        )

        legs: list[SelectedContractLeg] = []
        for leg_index in range(structure.leg_count):
            option_type = structure.option_types[leg_index]
            result = self.contract_selector.select_contract(
                contracts=candidates,
                underlying=signal.market.underlying,
                option_type=option_type.value,
                side=_CONTRACT_SELECTION_SIDE,
                expiry=expiry_value,
                custom_config=custom_config,
            )
            if result.get("selection_permission") != "ALLOW":
                _LOGGER.info(
                    "paper_trading_runtime.contract_selection.rejected",
                    extra={
                        "decision_id": decision.decision_id,
                        "leg_index": leg_index,
                        "reason": result.get("reason"),
                    },
                )
                return None
            selected = result["selected_contract"]
            legs.append(
                SelectedContractLeg(
                    leg_index=leg_index,
                    instrument_key=f"{selected['exchange']}:{selected['tradingsymbol']}",
                    strike=(
                        float(selected["strike"]) if selected["strike"] is not None else None
                    ),
                    option_type=OptionType(selected["option_type"]),
                    exchange=selected["exchange"],
                    lot_size=(
                        int(selected["lot_size"]) if selected["lot_size"] is not None else None
                    ),
                )
            )

        legs_tuple = tuple(legs)
        return ContractSelectionResult(
            selection_id=self._id_factory(),
            correlation_id=decision.correlation_id,
            underlying=signal.market.underlying,
            expiry=_parse_expiry_date(expiry_value),
            legs=legs_tuple,
            selection_fingerprint=_contract_selection_fingerprint(
                correlation_id=decision.correlation_id,
                underlying=signal.market.underlying,
                legs=legs_tuple,
            ),
        )

    def _build_position_sizing_hint(
        self, decision: PaperTradeDecision, contract_selection: ContractSelectionResult | None
    ) -> PositionSizingHint | None:
        """Package the decision's own already-computed sizing output for ExecutionEngine.

        ``PaperTradeDecision`` does not carry a ``PositionSizingHint``
        object itself, but it does carry the real numbers the existing
        ``position_sizing_engine.PositionSizingEngine.analyze`` call inside
        ``AIDecisionLoop`` already computed (``final_lots``,
        ``final_quantity``, ``approved_risk_budget``, ``approved_risk_pct``)
        — this only repackages those into the type ``ExecutionEngine``
        already expects, exactly as :meth:`_select_contracts` repackages the
        contract selector's own output. Returns ``None`` (no fabrication)
        when the decision carries no positive computed quantity, leaving
        ``ExecutionEngine``'s existing "Position sizing hint required in
        LIVE mode." gate to apply unchanged.

        Every leg receives the full computed quantity via a per-leg
        ``leg_{n}_quantity`` metadata override rather than
        ``proposed_units_hint`` alone — ``resolve_leg_quantity`` splits an
        undecorated ``proposed_units_hint`` evenly across legs, which would
        halve a 2-leg short strangle's size; each leg of a short
        strangle/iron condor/vertical spread trades the *same* computed
        quantity, not a fraction of it.
        """
        if decision.final_quantity <= 0:
            return None
        leg_count = (
            len(contract_selection.legs)
            if contract_selection is not None
            else (
                decision.risk_decision.trading_signal.structure_hint.leg_count
                if decision.risk_decision is not None
                and decision.risk_decision.trading_signal.structure_hint is not None
                else 1
            )
        )
        quantity_str = str(int(decision.final_quantity))
        metadata = {f"leg_{leg_index}_quantity": quantity_str for leg_index in range(leg_count)}
        return PositionSizingHint(
            hint_id=self._id_factory(),
            proposed_risk_amount=decision.approved_risk_budget or 0.0,
            proposed_risk_pct=decision.approved_risk_pct or 0.0,
            sizing_method="ai_decision_loop.position_sizing_engine",
            proposed_units_hint=float(decision.final_quantity),
            metadata=MappingProxyType(metadata),
        )

    def _execute_decision(self, decision: PaperTradeDecision) -> PaperTradeExecutionOutcome:
        if decision.status is not PaperTradeDecisionStatus.TRADE_CANDIDATE:
            return PaperTradeExecutionOutcome(
                decision=decision,
                execution_plan=None,
                paper_result=None,
                skipped_reason=f"decision_status={decision.status.value}",
            )
        if decision.risk_decision is None:
            _LOGGER.warning(
                "paper_trading_runtime.decision.missing_risk_decision",
                extra={"decision_id": decision.decision_id},
            )
            return PaperTradeExecutionOutcome(
                decision=decision,
                execution_plan=None,
                paper_result=None,
                skipped_reason="missing_risk_decision",
            )

        snapshot = self._market_data_engine.get_snapshot(decision.underlying)
        if snapshot is None:
            _LOGGER.warning(
                "paper_trading_runtime.decision.missing_snapshot",
                extra={"decision_id": decision.decision_id, "underlying": decision.underlying},
            )
            return PaperTradeExecutionOutcome(
                decision=decision,
                execution_plan=None,
                paper_result=None,
                skipped_reason="missing_snapshot",
            )

        contract_selection = self._select_contracts(decision, snapshot)
        position_sizing_hint = self._build_position_sizing_hint(decision, contract_selection)

        run_context = ExecutionRunContext(
            correlation_id=decision.correlation_id,
            as_of=decision.as_of,
            risk_decision=decision.risk_decision,
            market_snapshot=snapshot,
            contract_selection=contract_selection,
            position_sizing_hint=position_sizing_hint,
            execution_mode=self._decision_loop.config.execution_mode,
            reference_time=decision.as_of,
            tags=MappingProxyType({"source": "ai_decision_loop"}),
        )
        plan = self.execution_engine.plan_from_run_context(run_context)

        if plan.status is not ExecutionPlanStatus.READY:
            _LOGGER.info(
                "paper_trading_runtime.plan.not_ready",
                extra={
                    "decision_id": decision.decision_id,
                    "plan_id": plan.plan_id,
                    "plan_status": plan.status.value,
                },
            )
            return PaperTradeExecutionOutcome(
                decision=decision,
                execution_plan=plan,
                paper_result=None,
                skipped_reason=f"plan_status={plan.status.value}",
            )

        sim_context = PaperSimulationContext(
            correlation_id=plan.correlation_id,
            reference_time=decision.as_of,
            marks=MappingProxyType(_marks_from_plan(plan)),
            execution_mode=self._decision_loop.config.execution_mode,
            tags=MappingProxyType({"source": "ai_decision_loop"}),
        )
        result = self.paper_trading_runner.simulate_plan(plan, sim_context)
        _LOGGER.info(
            "paper_trading_runtime.paper_trade.simulated",
            extra={
                "decision_id": decision.decision_id,
                "plan_id": plan.plan_id,
                "execution_id": result.execution_id,
                "status": result.status.value,
            },
        )
        return PaperTradeExecutionOutcome(
            decision=decision, execution_plan=plan, paper_result=result, skipped_reason=None
        )


__all__ = [
    "MarketDataSnapshotSource",
    "PaperTradeExecutionOutcome",
    "PaperTradingRuntime",
]
