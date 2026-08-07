"""AI Decision Loop orchestration for THETA AI TRADER.

Wires the existing, already-implemented engines into one continuous,
read-only decision pipeline:

    live market snapshot -> (optional live OI cycle) -> market regime
    analysis -> strategy evaluation and selection -> risk validation ->
    position sizing -> a single :class:`PaperTradeDecision`.

Orchestration only — this module never reimplements OI, market regime,
strategy, risk, or position-sizing business logic; it calls the existing
engines' public APIs (:class:`live_oi_engine.LiveOIEngine` ->
:class:`oi_engine.OIEngine`, :class:`market_regime_engine.MarketRegimeEngine`,
:class:`strategy.strategy_evaluation_engine.StrategyEvaluationEngine`,
:class:`decision.trade_decision_engine.TradeDecisionEngine`,
:class:`risk.risk_engine.RiskEngine`,
:class:`position_sizing_engine.PositionSizingEngine`) exactly as designed.
It never places orders, never submits paper fills, and never mutates
strategy/risk/sizing state — see :mod:`paper_trading.paper_trading_runner`
for the (untouched) execution boundary.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Protocol

from decision.trade_decision_engine import (
    DecisionMode,
    DecisionRunContext,
    DecisionStatus,
    TradeDecisionEngine,
    TradeDecisionEngineConfig,
    TradeDecisionResult,
    UserPreferences,
    default_trade_decision_engine_config,
    default_user_preferences,
)
from market_data.market_snapshot import MarketSnapshot
from position_sizing_engine import PositionSizingEngine
from risk.risk_engine import (
    PortfolioExposureSummary,
    PortfolioSnapshot,
    PositionSizingHint,
    RiskDecisionResult,
    RiskEngine,
    RiskEngineConfig,
    RiskRunContext,
    RiskVerdict,
    UserRiskProfile,
    default_risk_engine_config,
    default_user_risk_profile,
    portfolio_fingerprint,
)
from strategy.registry import StrategyRegistry
from strategy.strategy_evaluation_engine import (
    EvaluationRunContext,
    StrategyEvaluationBundle,
    StrategyEvaluationEngine,
    StrategyEvaluationEngineConfig,
)
from strategy.signals import StrategyExecutionMode

AI_DECISION_LOOP_VERSION: Final[str] = "1.0.0"
AI_DECISION_LOOP_SCHEMA_VERSION: Final[str] = "1.0.0"
PLACEHOLDER: Final[str] = "—"

_LOGGER = logging.getLogger("decision.ai_decision_loop")


class DecisionLoopState(str, Enum):
    """Lifecycle state of :class:`AIDecisionLoop`."""

    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"


class DecisionLoopHealthStatus(str, Enum):
    """Aggregated decision-loop health band."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class PaperTradeDecisionStatus(str, Enum):
    """Terminal classification of one decision cycle's output."""

    TRADE_CANDIDATE = "trade_candidate"
    NO_TRADE = "no_trade"
    ABSTAINED = "abstained"
    BLOCKED_BY_RISK = "blocked_by_risk"
    NO_SNAPSHOT = "no_snapshot"
    ERROR = "error"


class AIDecisionLoopError(Exception):
    """Base exception for decision-loop configuration/lifecycle failures."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class AIDecisionLoopConfigurationError(AIDecisionLoopError):
    """Raised when :class:`AiDecisionLoopConfig` invariants fail."""


def _utc_now() -> datetime:
    """Return current UTC time with timezone info."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AiDecisionLoopConfig:
    """Immutable orchestration policy for :class:`AIDecisionLoop`.

    Attributes:
        underlying: Underlying to pull the live snapshot for each cycle.
        interval_seconds: Delay between cycles in the background loop.
        execution_mode: Execution mode forwarded to strategy evaluation
            and trade decision (their existing contract; unrelated to
            broker order placement, which this module never performs).
        default_risk_pct_per_trade: Fraction of portfolio equity proposed
            as the risk budget hint for one trade (orchestration-level
            sizing input, not a strategy/risk decision — the risk engine
            independently approves, reduces, or rejects it).
        default_stop_loss_per_unit: Per-unit stop-loss estimate passed to
            :class:`position_sizing_engine.PositionSizingEngine` when a
            strategy-specific estimate is not available upstream.
        default_margin_per_lot: Per-lot margin estimate passed to the
            position sizing engine when a live margin quote is not
            available upstream.
        is_expiry_day: Forwarded to position sizing's expiry-day lot cap.
        runner_kind: Audit tag (``cli``, ``paper``, ``test``).
        metadata: Non-secret audit metadata.
    """

    underlying: str = "NIFTY"
    interval_seconds: float = 5.0
    execution_mode: StrategyExecutionMode = StrategyExecutionMode.LIVE
    default_risk_pct_per_trade: float = 0.01
    default_stop_loss_per_unit: float = 0.0
    default_margin_per_lot: float = 0.0
    is_expiry_day: bool = False
    runner_kind: str = "cli"
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        """Validate configuration invariants."""
        if not self.underlying or not self.underlying.strip():
            raise AIDecisionLoopConfigurationError(
                "underlying must be non-empty.",
                code="AI_LOOP.CONFIG.INVALID",
            )
        if self.interval_seconds <= 0:
            raise AIDecisionLoopConfigurationError(
                "interval_seconds must be > 0.",
                code="AI_LOOP.CONFIG.INVALID",
            )
        if not (0.0 < self.default_risk_pct_per_trade <= 1.0):
            raise AIDecisionLoopConfigurationError(
                "default_risk_pct_per_trade must be in (0, 1].",
                code="AI_LOOP.CONFIG.INVALID",
            )
        if self.default_stop_loss_per_unit < 0 or self.default_margin_per_lot < 0:
            raise AIDecisionLoopConfigurationError(
                "default_stop_loss_per_unit/default_margin_per_lot must be >= 0.",
                code="AI_LOOP.CONFIG.INVALID",
            )


@dataclass(frozen=True)
class PaperTradeDecision:
    """Immutable, final output of one AI decision loop cycle.

    Assembled purely from already-computed upstream outputs (market
    regime, strategy selection, risk verdict, position sizing) — never
    fabricates a value. Fields are placeholders when a stage did not run
    (e.g. no snapshot available, or the pipeline stopped early because the
    decision engine abstained or the risk engine rejected).
    """

    decision_id: str
    correlation_id: str
    as_of: datetime
    underlying: str
    status: PaperTradeDecisionStatus
    market_regime: str = PLACEHOLDER
    regime_confidence: str = PLACEHOLDER
    decision_status: str = PLACEHOLDER
    strategy_id: str | None = None
    strategy_family: str | None = None
    confidence_score: float | None = None
    risk_verdict: str = PLACEHOLDER
    approved_risk_budget: float | None = None
    approved_risk_pct: float | None = None
    final_lots: int = 0
    final_quantity: int = 0
    sizing_reason: str | None = None
    reasons: tuple[str, ...] = ()
    trade_decision: TradeDecisionResult | None = None
    risk_decision: RiskDecisionResult | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class DecisionLoopHealthReport:
    """Aggregated decision-loop health snapshot."""

    report_id: str
    as_of: datetime
    state: DecisionLoopState
    overall_health: DecisionLoopHealthStatus
    cycles_run: int
    cycles_failed: int
    last_cycle_at: datetime | None
    last_cycle_duration_ms: float | None
    last_error_code: str | None
    last_decision_status: str | None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class DashboardStrategyRow:
    """One ranked strategy row for dashboard display."""

    family: str
    display_name: str
    status: str
    confidence: float | None
    eligibility: str
    reason: str


@dataclass(frozen=True)
class DashboardDecisionLoopStatus:
    """Dashboard-shaped decision-loop snapshot (current strategy, confidence, state).

    Shape matches what ``dashboard.dashboard_facade.DashboardIntegrationFacade``
    already soft-reads off its optional ``session.get_strategy_status()``
    (see ``_fetch_strategy_status``): ``market_regime``, ``active_strategy``,
    ``confidence_score``, ``evaluation_time``, and a ``strategies`` sequence
    of family/status/confidence rows.
    """

    market_regime: str
    active_strategy: str
    confidence_score: str
    evaluation_time: str
    state: str
    last_decision_status: str
    strategies: tuple[DashboardStrategyRow, ...] = ()


def _default_portfolio_snapshot(
    *,
    equity: float = 1_000_000.0,
    cash_available: float = 500_000.0,
    correlation_id: str,
    as_of: datetime,
) -> PortfolioSnapshot:
    """Build a flat, empty portfolio snapshot for standalone loop operation.

    Callers running against a real account should inject a live
    ``portfolio_provider`` instead (see :class:`AIDecisionLoop`); this is
    a safe, clearly-labeled default so the loop is fully functional
    without one.
    """
    exposure = PortfolioExposureSummary(
        gross_notional=0.0,
        net_notional_by_underlying=MappingProxyType({}),
        gross_notional_by_underlying=MappingProxyType({}),
        exposure_by_family=MappingProxyType({}),
        open_position_count=0,
        open_position_count_by_underlying=MappingProxyType({}),
    )
    snapshot = PortfolioSnapshot(
        snapshot_id=str(uuid.uuid4()),
        correlation_id=correlation_id,
        as_of=as_of,
        account_id="default",
        equity=equity,
        cash_available=cash_available,
        daily_realized_pnl=0.0,
        daily_unrealized_pnl=0.0,
        peak_equity=equity,
        consecutive_losses=0,
        open_positions=(),
        exposure_summary=exposure,
        portfolio_fingerprint="",
        margin_available_hint=cash_available,
    )
    return replace(snapshot, portfolio_fingerprint=portfolio_fingerprint(snapshot))


def build_dashboard_decision_loop_status(
    *,
    state: DecisionLoopState,
    latest_decision: PaperTradeDecision | None,
    latest_bundle: StrategyEvaluationBundle | None,
) -> DashboardDecisionLoopStatus:
    """Map decision-loop state to the dashboard's status shape.

    Read-only presentation mapping over already-computed outputs — never
    recomputes regime, strategy scores, or confidence.

    Args:
        state: Current loop lifecycle state.
        latest_decision: Most recent completed cycle output, if any.
        latest_bundle: Most recent strategy evaluation bundle, if any
            (used to populate the full ranked strategy list; the decision
            itself only carries the single selected strategy).

    Returns:
        Dashboard-shaped decision-loop status.
    """
    if latest_decision is None:
        return DashboardDecisionLoopStatus(
            market_regime=PLACEHOLDER,
            active_strategy=PLACEHOLDER,
            confidence_score=PLACEHOLDER,
            evaluation_time=PLACEHOLDER,
            state=state.value,
            last_decision_status=PLACEHOLDER,
            strategies=(),
        )

    confidence_score = (
        f"{latest_decision.confidence_score:.2f}"
        if latest_decision.confidence_score is not None
        else PLACEHOLDER
    )
    active_strategy = latest_decision.strategy_id or PLACEHOLDER

    strategies: list[DashboardStrategyRow] = []
    if latest_bundle is not None:
        for report in latest_bundle.ranked_reports:
            strategies.append(
                DashboardStrategyRow(
                    family=str(getattr(report.strategy_family, "value", report.strategy_family)),
                    display_name=report.display_name,
                    status=str(getattr(report.evaluation_status, "value", report.evaluation_status)),
                    confidence=getattr(report.confidence, "overall_score", None),
                    eligibility=str(getattr(report.outcome_class, "value", report.outcome_class)),
                    reason=report.reasons[0] if report.reasons else PLACEHOLDER,
                )
            )

    return DashboardDecisionLoopStatus(
        market_regime=latest_decision.market_regime,
        active_strategy=active_strategy,
        confidence_score=confidence_score,
        evaluation_time=latest_decision.as_of.strftime("%Y-%m-%d %H:%M:%S UTC"),
        state=state.value,
        last_decision_status=latest_decision.status.value,
        strategies=tuple(strategies),
    )


class SnapshotProvider(Protocol):
    """Structural protocol for the injected market-data snapshot source."""

    def get_snapshot(self, underlying: str) -> MarketSnapshot | None:
        """Return the latest cached snapshot for ``underlying``, or ``None``."""


class RegimeAnalyzer(Protocol):
    """Structural protocol for the injected market regime engine."""

    def analyze(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        """Return a regime analysis result mapping."""


class OIAnalyzer(Protocol):
    """Structural protocol for the injected live OI engine.

    Matches :class:`live_oi_engine.LiveOIEngine`, which already sequences
    live tick -> option chain -> :meth:`oi_engine.OIEngine.analyze_chain`
    for one cycle and reports its result via ``chain_analysis``.
    """

    def run_cycle(self) -> Mapping[str, Any]:
        """Run one live OI cycle and return its result mapping."""


class AIDecisionLoop:
    """Continuous, thread-safe AI decision loop orchestrator.

    Each cycle (triggered on demand via :meth:`run_once` or periodically
    by the background loop started with :meth:`start_loop`) reads the
    latest live market snapshot, runs market regime analysis, evaluates
    and selects a strategy, validates the selection against risk policy,
    sizes the position, and assembles a single immutable
    :class:`PaperTradeDecision`. Every stage delegates to the existing,
    unmodified engine for that stage; this class only sequences calls and
    threads outputs between them.
    """

    def __init__(
        self,
        config: AiDecisionLoopConfig,
        *,
        market_data_engine: SnapshotProvider,
        strategy_registry: StrategyRegistry,
        regime_engine: RegimeAnalyzer | None = None,
        oi_engine: OIAnalyzer | None = None,
        strategy_evaluation_engine: StrategyEvaluationEngine | None = None,
        trade_decision_engine: TradeDecisionEngine | None = None,
        risk_engine: RiskEngine | None = None,
        position_sizing_engine: PositionSizingEngine | None = None,
        portfolio_provider: Callable[[], PortfolioSnapshot] | None = None,
        user_risk_profile_provider: Callable[[], UserRiskProfile] | None = None,
        user_preferences: UserPreferences | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        """Construct the decision loop.

        Args:
            config: Immutable orchestration policy.
            market_data_engine: Snapshot source (e.g.
                :class:`broker.market_data_streaming.MarketDataStreamingEngine`).
            strategy_registry: Already-configured registry of production
                strategy plugins (never constructed or mutated here).
            regime_engine: Market regime analyzer; defaults to a fresh
                :class:`market_regime_engine.MarketRegimeEngine`.
            oi_engine: Live OI cycle source (e.g.
                :class:`live_oi_engine.LiveOIEngine`); its ``chain_analysis``
                output is threaded into ``regime_engine.analyze(oi_analysis=...)``
                each cycle. Optional — when omitted, regime analysis simply
                runs without OI input (unchanged prior behavior), since
                constructing a live OI engine requires broker credentials
                this loop must not assume are configured.
            strategy_evaluation_engine: Defaults to a fresh engine bound
                to ``strategy_registry``.
            trade_decision_engine: Defaults to a fresh engine with
                default configuration.
            risk_engine: Defaults to a fresh engine with default
                configuration.
            position_sizing_engine: Defaults to a fresh
                :class:`position_sizing_engine.PositionSizingEngine`.
            portfolio_provider: Optional callable returning a live
                :class:`risk.risk_engine.PortfolioSnapshot`; defaults to a
                flat/empty snapshot when omitted.
            user_risk_profile_provider: Optional callable returning a live
                :class:`risk.risk_engine.UserRiskProfile`; defaults to a
                MODERATE-tier profile when omitted.
            user_preferences: Defaults to
                :func:`decision.trade_decision_engine.default_user_preferences`.
            clock: Injectable clock for deterministic timestamps/tests.
            id_factory: Injectable UUID factory for deterministic tests.
        """
        self._config = config
        self._market_data_engine = market_data_engine
        self._strategy_registry = strategy_registry
        self._clock = clock or _utc_now
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

        if regime_engine is None:
            from market_regime_engine import MarketRegimeEngine

            regime_engine = MarketRegimeEngine()
        self._regime_engine = regime_engine
        self._oi_engine = oi_engine

        self._strategy_evaluation_engine = strategy_evaluation_engine or (
            StrategyEvaluationEngine(StrategyEvaluationEngineConfig(), strategy_registry)
        )
        self._trade_decision_engine = trade_decision_engine or TradeDecisionEngine(
            default_trade_decision_engine_config()
        )
        self._risk_engine = risk_engine or RiskEngine(default_risk_engine_config())
        self._position_sizing_engine = position_sizing_engine or PositionSizingEngine()

        self._portfolio_provider = portfolio_provider
        self._user_risk_profile_provider = user_risk_profile_provider or (
            lambda: default_user_risk_profile()
        )
        self._user_preferences = user_preferences or default_user_preferences()

        self._lock = threading.RLock()
        self._state = DecisionLoopState.CREATED
        self._latest_decision: PaperTradeDecision | None = None
        self._latest_bundle: StrategyEvaluationBundle | None = None
        self._cycles_run = 0
        self._cycles_failed = 0
        self._last_cycle_at: datetime | None = None
        self._last_cycle_duration_ms: float | None = None
        self._last_error_code: str | None = None
        self._last_error_message: str | None = None

        self._loop_thread: threading.Thread | None = None
        self._loop_stop_event = threading.Event()

        self._callbacks_lock = threading.RLock()
        self._callbacks: list[Callable[[PaperTradeDecision], None]] = []

    @property
    def config(self) -> AiDecisionLoopConfig:
        """Return the immutable orchestration configuration."""
        return self._config

    # -- Lifecycle -----------------------------------------------------

    def start(self) -> None:
        """Transition to ``RUNNING``. Idempotent."""
        with self._lock:
            if self._state is not DecisionLoopState.RUNNING:
                self._state = DecisionLoopState.RUNNING

    def stop(self) -> None:
        """Transition to ``STOPPED``. Idempotent. Does not stop the background thread."""
        with self._lock:
            self._state = DecisionLoopState.STOPPED

    def get_state(self) -> DecisionLoopState:
        """Return the current lifecycle state."""
        with self._lock:
            return self._state

    def start_loop(self, *, interval_seconds: float | None = None) -> None:
        """Start the continuous background decision loop.

        Calls :meth:`start` and spawns a daemon thread that calls
        :meth:`run_once` on the configured (or overridden) interval.
        Idempotent — calling again while a loop thread is already running
        is a no-op.

        Args:
            interval_seconds: Optional override for
                ``config.interval_seconds``.
        """
        interval = interval_seconds if interval_seconds is not None else self._config.interval_seconds
        if interval <= 0:
            raise AIDecisionLoopConfigurationError(
                "interval_seconds must be > 0.",
                code="AI_LOOP.CONFIG.INVALID",
            )
        self.start()
        with self._lock:
            if self._loop_thread is not None and self._loop_thread.is_alive():
                return
            self._loop_stop_event.clear()

        def _run() -> None:
            while not self._loop_stop_event.wait(interval):
                if self.get_state() is not DecisionLoopState.RUNNING:
                    continue
                try:
                    self.run_once()
                except Exception:  # noqa: BLE001 - loop must never die
                    _LOGGER.exception("ai_decision_loop.cycle.unhandled_failure")

        thread = threading.Thread(
            target=_run,
            name="ai-decision-loop",
            daemon=True,
        )
        with self._lock:
            self._loop_thread = thread
        thread.start()

    def stop_loop(self, *, timeout: float = 2.0) -> None:
        """Signal and join the background loop thread, if running.

        Safe to call when no loop thread is running.

        Args:
            timeout: Maximum seconds to wait for the thread to exit.
        """
        self._loop_stop_event.set()
        with self._lock:
            thread = self._loop_thread
            self._loop_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def shutdown(self) -> None:
        """Gracefully stop the background loop and transition to ``STOPPED``.

        Safe to call multiple times.
        """
        self.stop_loop()
        self.stop()

    # -- Decision cycle --------------------------------------------------

    def run_once(self) -> PaperTradeDecision:
        """Execute exactly one decision cycle synchronously. Thread-safe.

        Never raises for expected upstream conditions (no snapshot,
        abstained decision, rejected risk) — those are reported via
        ``PaperTradeDecision.status``. Unexpected exceptions from any
        stage are caught, recorded, and reported as an ``ERROR`` status
        decision so the background loop can keep running.

        Returns:
            The assembled decision for this cycle.
        """
        started = self._clock()
        start_perf = time.perf_counter()
        correlation_id = self._id_factory()
        try:
            decision = self._run_cycle(correlation_id=correlation_id, started=started)
            with self._lock:
                self._cycles_run += 1
                self._last_error_code = None
                self._last_error_message = None
        except Exception as exc:  # noqa: BLE001 - orchestration must not crash
            decision = self._error_decision(
                correlation_id=correlation_id,
                as_of=started,
                code="AI_LOOP.CYCLE.UNHANDLED",
                message=str(exc),
            )
            with self._lock:
                self._cycles_run += 1
                self._cycles_failed += 1
                self._last_error_code = "AI_LOOP.CYCLE.UNHANDLED"
                self._last_error_message = str(exc)

        completed = self._clock()
        duration_ms = (time.perf_counter() - start_perf) * 1000
        with self._lock:
            self._latest_decision = decision
            self._last_cycle_at = completed
            self._last_cycle_duration_ms = duration_ms

        self._dispatch_callbacks(decision)
        return decision

    def _run_cycle(self, *, correlation_id: str, started: datetime) -> PaperTradeDecision:
        """Run the full regime -> strategy -> risk -> sizing pipeline once."""
        snapshot = self._market_data_engine.get_snapshot(self._config.underlying)
        if snapshot is None:
            return PaperTradeDecision(
                decision_id=self._id_factory(),
                correlation_id=correlation_id,
                as_of=started,
                underlying=self._config.underlying,
                status=PaperTradeDecisionStatus.NO_SNAPSHOT,
                reasons=("No live market snapshot available for underlying.",),
            )

        regime_label, regime_confidence, regime_tags = self._analyze_regime()

        registry_snapshot = self._strategy_registry.snapshot()
        eval_ctx = EvaluationRunContext(
            correlation_id=correlation_id,
            as_of=started,
            snapshot=snapshot,
            registry_snapshot=registry_snapshot,
            execution_mode=self._config.execution_mode,
            tags=MappingProxyType(regime_tags),
        )
        bundle = self._strategy_evaluation_engine.evaluate_bundle(eval_ctx)
        with self._lock:
            self._latest_bundle = bundle

        decision_ctx = DecisionRunContext(
            correlation_id=correlation_id,
            as_of=started,
            bundle=bundle,
            mode=DecisionMode.AUTONOMOUS,
            preferences=self._user_preferences,
            execution_mode=self._config.execution_mode,
            reference_time=started,
        )
        trade_decision = self._trade_decision_engine.decide(decision_ctx)

        base_kwargs: dict[str, Any] = dict(
            decision_id=self._id_factory(),
            correlation_id=correlation_id,
            as_of=started,
            underlying=self._config.underlying,
            market_regime=regime_label,
            regime_confidence=regime_confidence,
            decision_status=trade_decision.decision_status.value,
            trade_decision=trade_decision,
        )

        if trade_decision.decision_status is not DecisionStatus.SELECTED:
            reasons = tuple(reason.message for reason in trade_decision.reasons)
            return PaperTradeDecision(
                status=PaperTradeDecisionStatus.ABSTAINED,
                reasons=reasons,
                **base_kwargs,
            )

        strategy_id = trade_decision.selected_strategy_id
        strategy_family = (
            str(getattr(trade_decision.selected_report.strategy_family, "value", None))
            if trade_decision.selected_report is not None
            else None
        )
        confidence_score = trade_decision.confidence.overall_score
        base_kwargs.update(
            strategy_id=strategy_id,
            strategy_family=strategy_family,
            confidence_score=confidence_score,
        )

        portfolio = (
            self._portfolio_provider()
            if self._portfolio_provider is not None
            else _default_portfolio_snapshot(correlation_id=correlation_id, as_of=started)
        )
        user_risk_profile = self._user_risk_profile_provider()
        sizing_hint = PositionSizingHint(
            hint_id=self._id_factory(),
            proposed_risk_amount=portfolio.equity * self._config.default_risk_pct_per_trade,
            proposed_risk_pct=self._config.default_risk_pct_per_trade * 100.0,
            sizing_method="ai_decision_loop.equity_pct_v1",
        )
        risk_ctx = RiskRunContext(
            correlation_id=correlation_id,
            as_of=started,
            trade_decision=trade_decision,
            portfolio=portfolio,
            user_risk_profile=user_risk_profile,
            position_sizing_hint=sizing_hint,
            execution_mode=self._config.execution_mode,
            reference_time=started,
        )
        risk_decision = self._risk_engine.review(risk_ctx)
        base_kwargs.update(
            risk_verdict=risk_decision.verdict.value,
            approved_risk_budget=risk_decision.approved_risk_budget,
            approved_risk_pct=risk_decision.approved_risk_pct,
            risk_decision=risk_decision,
        )

        if risk_decision.verdict is not RiskVerdict.APPROVED:
            reasons = tuple(reason.message for reason in risk_decision.reasons)
            return PaperTradeDecision(
                status=PaperTradeDecisionStatus.BLOCKED_BY_RISK,
                reasons=reasons,
                **base_kwargs,
            )

        lot_size = snapshot.option_chain.metadata.lot_size
        available_margin = (
            portfolio.margin_available_hint
            if portfolio.margin_available_hint is not None
            else portfolio.cash_available
        )
        sizing_result = self._position_sizing_engine.analyze(
            risk_analysis={
                "risk_permission": "ALLOW",
                "entry_allowed": True,
                "allowed_risk_rupees": risk_decision.approved_risk_budget or 0.0,
            },
            lot_size=lot_size,
            stop_loss_per_unit=self._config.default_stop_loss_per_unit,
            margin_per_lot=self._config.default_margin_per_lot,
            available_margin=available_margin,
            is_expiry_day=self._config.is_expiry_day,
        )
        final_lots = int(sizing_result.get("final_lots", 0) or 0)
        final_quantity = int(sizing_result.get("final_quantity", final_lots * lot_size) or 0)
        sizing_reason = sizing_result.get("reason")
        status = (
            PaperTradeDecisionStatus.TRADE_CANDIDATE
            if final_lots > 0
            else PaperTradeDecisionStatus.NO_TRADE
        )
        return PaperTradeDecision(
            status=status,
            final_lots=final_lots,
            final_quantity=final_quantity,
            sizing_reason=sizing_reason,
            **base_kwargs,
        )

    def _run_oi_cycle(self) -> Mapping[str, Any] | None:
        """Run one live OI cycle defensively and return its chain analysis.

        Returns ``None`` (never raises) when no OI engine is configured, the
        cycle could not produce a comparable pair yet (e.g. first run), or
        the OI engine fails — matching :meth:`_analyze_regime`'s treatment
        of regime analysis as advisory input, never a hard dependency.
        """
        if self._oi_engine is None:
            return None
        try:
            result = self._oi_engine.run_cycle()
        except Exception:  # noqa: BLE001 - OI input is advisory, never fatal
            _LOGGER.exception("ai_decision_loop.oi_cycle.failed")
            return None
        if not isinstance(result, Mapping) or not result.get("analysis_ready"):
            return None
        chain_analysis = result.get("chain_analysis")
        return chain_analysis if isinstance(chain_analysis, Mapping) else None

    def _analyze_regime(self) -> tuple[str, str, dict[str, str]]:
        """Run regime analysis defensively; never blocks the cycle on failure.

        Feeds the existing live OI engine's :meth:`oi_engine.OIEngine.analyze_chain`
        output (via :meth:`_run_oi_cycle`) into
        ``regime_engine.analyze(oi_analysis=...)`` so the already-implemented
        OI intelligence participates in regime scoring, without this loop
        reimplementing any OI or regime logic itself.

        Returns:
            ``(regime_label, regime_confidence_band, tags)`` where ``tags``
            is suitable for :attr:`EvaluationRunContext.tags` — this is how
            regime output is threaded into strategy evaluation without
            altering its input contract.
        """
        oi_analysis = self._run_oi_cycle()
        try:
            result = self._regime_engine.analyze(oi_analysis=oi_analysis)
        except Exception:  # noqa: BLE001 - regime is advisory, never fatal
            _LOGGER.exception("ai_decision_loop.regime.failed")
            return PLACEHOLDER, PLACEHOLDER, {}
        if not isinstance(result, Mapping):
            return PLACEHOLDER, PLACEHOLDER, {}
        label = str(result.get("regime", PLACEHOLDER)) or PLACEHOLDER
        confidence = str(result.get("regime_confidence", PLACEHOLDER)) or PLACEHOLDER
        tags = {"regime": label, "regime_confidence": confidence}
        permission = result.get("trade_permission")
        if permission is not None:
            tags["regime_trade_permission"] = str(permission)
        return label, confidence, tags

    def _error_decision(
        self,
        *,
        correlation_id: str,
        as_of: datetime,
        code: str,
        message: str,
    ) -> PaperTradeDecision:
        """Build an ERROR-status decision for an unhandled cycle failure."""
        return PaperTradeDecision(
            decision_id=self._id_factory(),
            correlation_id=correlation_id,
            as_of=as_of,
            underlying=self._config.underlying,
            status=PaperTradeDecisionStatus.ERROR,
            reasons=(message,),
            metadata=MappingProxyType({"error_code": code}),
        )

    def _dispatch_callbacks(self, decision: PaperTradeDecision) -> None:
        with self._callbacks_lock:
            callbacks = tuple(self._callbacks)
        for callback in callbacks:
            try:
                callback(decision)
            except Exception:  # noqa: BLE001 - one bad subscriber must not break others
                _LOGGER.exception("ai_decision_loop.callback.failed")

    def add_decision_callback(self, callback: Callable[[PaperTradeDecision], None]) -> None:
        """Register a callback invoked with each new decision."""
        with self._callbacks_lock:
            self._callbacks.append(callback)

    def remove_decision_callback(self, callback: Callable[[PaperTradeDecision], None]) -> None:
        """Unregister a previously registered decision callback."""
        with self._callbacks_lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    # -- Reads -------------------------------------------------------------

    def get_latest_decision(self) -> PaperTradeDecision | None:
        """Return the most recently completed decision, if any."""
        with self._lock:
            return self._latest_decision

    def get_latest_bundle(self) -> StrategyEvaluationBundle | None:
        """Return the most recent strategy evaluation bundle, if any."""
        with self._lock:
            return self._latest_bundle

    def get_health(self) -> DecisionLoopHealthReport:
        """Return an aggregated decision-loop health snapshot."""
        with self._lock:
            state = self._state
            cycles_run = self._cycles_run
            cycles_failed = self._cycles_failed
            last_cycle_at = self._last_cycle_at
            last_cycle_duration_ms = self._last_cycle_duration_ms
            last_error_code = self._last_error_code
            latest_decision = self._latest_decision

        issues: list[str] = []
        if state is DecisionLoopState.CREATED:
            overall = DecisionLoopHealthStatus.UNKNOWN
        elif last_error_code is not None and latest_decision is not None and (
            latest_decision.status is PaperTradeDecisionStatus.ERROR
        ):
            overall = DecisionLoopHealthStatus.UNHEALTHY
            issues.append(f"Last cycle failed: {last_error_code}")
        elif cycles_run > 0 and cycles_failed / cycles_run > 0.5:
            overall = DecisionLoopHealthStatus.DEGRADED
            issues.append("More than half of recent cycles failed.")
        elif state is DecisionLoopState.RUNNING:
            overall = DecisionLoopHealthStatus.HEALTHY
        else:
            overall = DecisionLoopHealthStatus.UNKNOWN

        return DecisionLoopHealthReport(
            report_id=self._id_factory(),
            as_of=self._clock(),
            state=state,
            overall_health=overall,
            cycles_run=cycles_run,
            cycles_failed=cycles_failed,
            last_cycle_at=last_cycle_at,
            last_cycle_duration_ms=last_cycle_duration_ms,
            last_error_code=last_error_code,
            last_decision_status=latest_decision.status.value if latest_decision else None,
            issues=tuple(issues),
        )

    def get_dashboard_status(self) -> DashboardDecisionLoopStatus:
        """Return the dashboard-shaped status (current strategy, confidence, state)."""
        with self._lock:
            state = self._state
            latest_decision = self._latest_decision
            latest_bundle = self._latest_bundle
        return build_dashboard_decision_loop_status(
            state=state,
            latest_decision=latest_decision,
            latest_bundle=latest_bundle,
        )

    def get_strategy_status(self) -> DashboardDecisionLoopStatus:
        """Alias for :meth:`get_dashboard_status`.

        Matches the accessor name ``dashboard.dashboard_facade.DashboardIntegrationFacade``
        soft-reads (``session.get_strategy_status()``) so this loop can be
        wired in directly as (part of) the dashboard's upstream session.
        """
        return self.get_dashboard_status()


__all__ = [
    "AI_DECISION_LOOP_VERSION",
    "AI_DECISION_LOOP_SCHEMA_VERSION",
    "PLACEHOLDER",
    "DecisionLoopState",
    "DecisionLoopHealthStatus",
    "PaperTradeDecisionStatus",
    "AIDecisionLoopError",
    "AIDecisionLoopConfigurationError",
    "AiDecisionLoopConfig",
    "PaperTradeDecision",
    "DecisionLoopHealthReport",
    "DashboardStrategyRow",
    "DashboardDecisionLoopStatus",
    "SnapshotProvider",
    "RegimeAnalyzer",
    "OIAnalyzer",
    "AIDecisionLoop",
    "build_dashboard_decision_loop_status",
]
