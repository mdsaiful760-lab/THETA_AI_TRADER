"""Unit tests for apme.adaptive_position_management_engine."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from apme.adaptive_position_management_engine import (
    ERROR_CONFIG_INVALID,
    ERROR_CONTEXT_ACCOUNT_MISMATCH,
    ERROR_CONTEXT_CORRELATION_MISMATCH,
    ERROR_CONTEXT_INVALID,
    ERROR_CONTEXT_NAIVE_TIMESTAMP,
    ERROR_RESULT_INVALID,
    ERROR_SERIALIZATION_MALFORMED,
    ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
    ERROR_SNAPSHOT_INVALID,
    APMEConfig,
    APMEConfigurationError,
    APMEEvaluationContext,
    APMEEvaluationStageId,
    APMEEvaluationStatus,
    APMEEventType,
    APMEPositionContext,
    APMEValidationError,
    AdaptivePositionManagementEngine,
    HealthStatus,
    ManagementAction,
    NewsEventFlag,
    SessionContext,
    SignalManagementMetadata,
    STAGE_ORDER,
    TrendHints,
    VolatilityHints,
    assert_valid_apme_decision_report,
    compute_decision_fingerprint,
    compute_exit_probability,
    compute_health_fingerprint,
    compute_position_health,
    compute_quality_fingerprint,
    compute_report_fingerprint,
    config_fingerprint,
    default_apme_config,
    default_exit_prob_weights,
    default_health_weights,
    default_quality_weights,
    deserialize_apme_decision_report,
    deserialize_position_management_decision,
    serialize_apme_decision_report,
    serialize_position_management_decision,
    validate_apme_decision_report,
    validate_evaluation_context,
)
from core.event_bus import EventBus
from portfolio.portfolio_manager import (
    PortfolioEvent,
    PortfolioEventType,
    PortfolioManager,
    PortfolioUpdateStatus,
    PositionGreekHint,
    compute_snapshot_fingerprint,
)
from portfolio.position_manager import Position, PositionSide
from strategy.signals import (
    StopLossHint,
    StopLossHintType,
    StrategyExecutionMode,
    StrategyFamily,
)
from tests.test_portfolio_manager import (
    fast_config as portfolio_fast_config,
    fixed_as_of,
    make_ingest_context,
    make_position,
    make_position_snapshot,
)

IST = ZoneInfo("Asia/Kolkata")


def apme_fast_config(**overrides: object) -> APMEConfig:
    """Build fast deterministic APME configuration for tests."""
    base = default_apme_config()
    defaults: dict[str, object] = {"decision_cooldown_seconds": 0}
    defaults.update(overrides)
    return replace(base, **defaults)


def ingest_portfolio_snapshot(
    *,
    positions: tuple[Position, ...] | None = None,
    fingerprint: str = "pf-apme-base",
    equity_hint: float = 1_000_000.0,
) -> object:
    """Build PortfolioSnapshot via PortfolioManager ingest."""
    mgr = PortfolioManager(portfolio_fast_config(require_account_hints=False))
    pos_list = positions if positions is not None else (make_position(),)
    price_hints = {
        position.instrument_key: round(position.average_entry_price + 5.0, 2)
        for position in pos_list
    }
    pos_snap = make_position_snapshot(*pos_list, fingerprint=fingerprint)
    ctx = make_ingest_context(
        equity_hint=equity_hint,
        correlation_id="corr-1",
        price_hints=MappingProxyType(price_hints),
    )
    result = mgr.ingest_position_snapshot(pos_snap, ctx)
    assert result.status in (PortfolioUpdateStatus.APPLIED, PortfolioUpdateStatus.PARTIAL)
    return result.snapshot


def empty_portfolio_snapshot(fingerprint: str = "pf-apme-empty") -> object:
    """Build empty portfolio snapshot."""
    mgr = PortfolioManager(portfolio_fast_config(require_account_hints=False))
    pos_snap = make_position_snapshot(fingerprint=fingerprint)
    pos_snap = replace(
        pos_snap,
        positions=(),
        open_position_count=0,
        aggregate_unrealized_pnl=0.0,
        aggregate_quantity_by_underlying=MappingProxyType({}),
    )
    ctx = make_ingest_context(correlation_id="corr-1")
    result = mgr.ingest_position_snapshot(pos_snap, ctx)
    assert result.status is PortfolioUpdateStatus.APPLIED
    return result.snapshot


def make_apme_context(
    snapshot: object,
    *,
    correlation_id: str = "corr-1",
    reference_time: datetime | None = None,
    execution_mode: StrategyExecutionMode = StrategyExecutionMode.BACKTEST,
    account_id: str = "acct-1",
    price_hints: MappingProxyType | None = None,
    underlying_marks: MappingProxyType | None = None,
    greek_hints: MappingProxyType | None = None,
    volatility_hints: VolatilityHints | None = None,
    trend_hints: MappingProxyType | None = None,
    news_flags: tuple[NewsEventFlag, ...] = (),
    signal_metadata: MappingProxyType | None = None,
    session_context: SessionContext | None = None,
) -> APMEEvaluationContext:
    """Build APME evaluation context aligned with portfolio snapshot."""
    instrument_key = "NFO:NIFTY24AUG25000CE"
    if snapshot.positions:
        instrument_key = snapshot.positions[0].instrument_key
    return APMEEvaluationContext(
        correlation_id=correlation_id,
        reference_time=reference_time or fixed_as_of(),
        execution_mode=execution_mode,
        account_id=account_id,
        portfolio_snapshot_id=snapshot.snapshot_id,
        price_hints=price_hints or MappingProxyType({instrument_key: 130.0}),
        underlying_marks=underlying_marks or MappingProxyType({}),
        greek_hints=greek_hints or MappingProxyType({}),
        volatility_hints=volatility_hints,
        trend_hints=trend_hints or MappingProxyType({}),
        news_flags=news_flags,
        signal_metadata=signal_metadata or MappingProxyType({}),
        session_context=session_context,
        tags=MappingProxyType({}),
    )


def make_position_context(
    snapshot: object,
    *,
    dte: int | None = None,
    expiry: str | None = None,
    metadata_extra: dict[str, str] | None = None,
    mark_price: float | None = 130.0,
    underlying_mark: float | None = None,
    greek_hint: PositionGreekHint | None = None,
    signal_metadata: SignalManagementMetadata | None = None,
    trend_hint: TrendHints | None = None,
) -> APMEPositionContext:
    """Build hydrated position context for helper unit tests."""
    assert snapshot.positions
    position = snapshot.positions[0]
    meta = dict(position.metadata)
    if expiry is not None:
        meta["expiry"] = expiry
    if dte is not None:
        meta["dte"] = str(dte)
    if metadata_extra:
        meta.update(metadata_extra)
    position = replace(position, metadata=MappingProxyType(meta), expiry=expiry or position.expiry)
    return APMEPositionContext(
        position=position,
        mark_price=mark_price,
        underlying_mark=underlying_mark,
        greek_hint=greek_hint,
        signal_metadata=signal_metadata,
        trend_hint=trend_hint,
        dte=dte if dte is not None else 24,
        is_short_premium=position.side.lower() == "short",
        position_group_id=position.metadata.get("position_group_id"),
    )


@pytest.fixture
def engine() -> AdaptivePositionManagementEngine:
    """Return APME instance with fast test configuration."""
    return AdaptivePositionManagementEngine(apme_fast_config())


class TestConfiguration:
    def test_invalid_decision_cooldown(self) -> None:
        with pytest.raises(APMEConfigurationError) as exc:
            APMEConfig(decision_cooldown_seconds=-1)
        assert exc.value.code == ERROR_CONFIG_INVALID
        assert exc.value.field == "decision_cooldown_seconds"

    def test_invalid_hint_max_age(self) -> None:
        with pytest.raises(APMEConfigurationError) as exc:
            APMEConfig(hint_max_age_seconds=-1)
        assert exc.value.code == ERROR_CONFIG_INVALID
        assert exc.value.field == "hint_max_age_seconds"

    def test_default_config_factory(self) -> None:
        config = default_apme_config()
        assert config.strict_correlation is True
        assert config.idempotent_evaluate is True
        assert config.decision_cooldown_seconds == 60

    def test_default_weight_factories(self) -> None:
        health = default_health_weights()
        quality = default_quality_weights()
        exit_prob = default_exit_prob_weights()
        assert abs(sum(health.values()) - 1.0) < 0.01
        assert abs(sum(quality.values()) - 1.0) < 0.01
        assert "health_inverse" in exit_prob

    def test_config_fingerprint_deterministic(self) -> None:
        first = config_fingerprint(apme_fast_config())
        second = config_fingerprint(apme_fast_config())
        assert first == second


class TestHelperFunctions:
    def test_compute_health_fingerprint(self) -> None:
        scores = {"structural": 0.8, "liquidity": 0.7}
        fp = compute_health_fingerprint("pos-1", scores, fixed_as_of())
        assert len(fp) == 64

    def test_compute_quality_fingerprint(self) -> None:
        components = {"profitability": 0.6, "overall": 0.55}
        fp = compute_quality_fingerprint("pos-1", components, fixed_as_of())
        assert len(fp) == 64

    def test_compute_decision_fingerprint(self) -> None:
        fp = compute_decision_fingerprint(
            "pos-1",
            ManagementAction.HOLD,
            MappingProxyType({"health": "abc"}),
        )
        assert len(fp) == 64

    def test_compute_position_health_short_dte(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-health-dte")
        ctx = make_position_context(snapshot, dte=1, expiry="2026-08-05")
        health = compute_position_health(ctx, apme_fast_config(), reference_time=fixed_as_of())
        assert health.health_status in (HealthStatus.STRESSED, HealthStatus.CRITICAL, HealthStatus.WATCH)
        assert any(i.issue_code.startswith("APME.HEALTH") for i in health.issues)

    def test_compute_position_health_missing_mark(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-health-mark")
        ctx = make_position_context(snapshot, mark_price=None)
        health = compute_position_health(ctx, apme_fast_config(), reference_time=fixed_as_of())
        assert any(i.issue_code == "APME.HEALTH.LIQUIDITY.MARK_MISSING" for i in health.issues)

    def test_compute_position_health_delta_stress(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-health-delta")
        greek = PositionGreekHint(
            position_id=snapshot.positions[0].position_id,
            as_of=fixed_as_of(),
            delta=0.45,
        )
        ctx = make_position_context(snapshot, greek_hint=greek)
        health = compute_position_health(ctx, apme_fast_config(), reference_time=fixed_as_of())
        assert health.greek_health_score is not None
        assert any(i.issue_code == "APME.HEALTH.GREEK.DELTA_STRESS" for i in health.issues)

    def test_compute_position_health_strike_distance(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-health-strike")
        ctx = make_position_context(
            snapshot,
            metadata_extra={"short_strike_distance_pct": "0.3"},
        )
        health = compute_position_health(ctx, apme_fast_config(), reference_time=fixed_as_of())
        assert any(
            i.issue_code == "APME.HEALTH.DISTANCE.SHORT_STRIKE_TESTED" for i in health.issues
        )

    def test_compute_exit_probability_with_vol_crisis(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-exit-prob")
        ctx = make_position_context(snapshot, dte=1, expiry="2026-08-05")
        health = compute_position_health(ctx, apme_fast_config(), reference_time=fixed_as_of())
        vol = VolatilityHints(as_of=fixed_as_of(), vix_regime="CRISIS", vix_level=40.0)
        from apme.adaptive_position_management_engine import ExitHint, ActionUrgency, APMEEngineId
        from strategy.signals import ExitTriggerType

        hint = ExitHint(
            engine_id=APMEEngineId.TIME_EXIT,
            exit_fraction=1.0,
            urgency=ActionUrgency.HIGH,
            trigger=ExitTriggerType.EXPIRY_APPROACH,
            reason_code="APME.TIME.EXIT.DTE_THRESHOLD",
            message="DTE threshold.",
        )
        ep = compute_exit_probability(
            health,
            (hint,),
            vol,
            60,
            apme_fast_config(),
        )
        assert 0.0 <= ep.probability <= 1.0
        assert ep.contributing_factors["vol_stress"] == 1.0


class TestInputGate:
    def test_reject_naive_timestamp(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-naive")
        naive = datetime(2026, 8, 4, 10, 0, 0)
        context = make_apme_context(snapshot, reference_time=naive)
        validation = validate_evaluation_context(context, snapshot, apme_fast_config())
        assert not validation.is_valid
        assert validation.errors[0].code == ERROR_CONTEXT_NAIVE_TIMESTAMP
        engine_validation = engine.validate_evaluation_context(context, snapshot)
        assert not engine_validation.is_valid
        assert engine_validation.errors[0].code == ERROR_CONTEXT_NAIVE_TIMESTAMP

    def test_reject_correlation_mismatch(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-corr")
        context = make_apme_context(snapshot, correlation_id="other-corr")
        report = engine.evaluate(snapshot, context)
        assert report.status is APMEEvaluationStatus.REJECTED
        assert report.primary_error_code == ERROR_CONTEXT_CORRELATION_MISMATCH

    def test_reject_empty_correlation_id(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-empty-corr")
        context = make_apme_context(snapshot, correlation_id="")
        engine = AdaptivePositionManagementEngine(apme_fast_config())
        report = engine.evaluate(snapshot, context)
        assert report.status is APMEEvaluationStatus.REJECTED
        assert report.primary_error_code == ERROR_CONTEXT_CORRELATION_MISMATCH

    def test_reject_account_mismatch_live(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-acct")
        context = make_apme_context(
            snapshot,
            execution_mode=StrategyExecutionMode.LIVE,
            account_id="wrong-account",
        )
        engine = AdaptivePositionManagementEngine(apme_fast_config())
        report = engine.evaluate(snapshot, context)
        assert report.status is APMEEvaluationStatus.REJECTED
        assert report.primary_error_code == ERROR_CONTEXT_ACCOUNT_MISMATCH

    def test_invalid_snapshot_open_count_mismatch(
        self, engine: AdaptivePositionManagementEngine
    ) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-bad-count")
        bad_metrics = replace(snapshot.metrics, open_position_count=99)
        bad_snapshot = replace(snapshot, metrics=bad_metrics)
        report = engine.evaluate(bad_snapshot, make_apme_context(bad_snapshot))
        failed = report.pipeline_summary.failed_stage_id
        assert failed is APMEEvaluationStageId.SNAPSHOT_INTEGRITY
        integrity_stage = next(
            s
            for s in report.pipeline_summary.stages
            if s.stage_id is APMEEvaluationStageId.SNAPSHOT_INTEGRITY
        )
        assert integrity_stage.rejection_code == ERROR_SNAPSHOT_INVALID


class TestEvaluationPipeline:
    def test_empty_portfolio_completed(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = empty_portfolio_snapshot("pf-apme-empty-eval")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        assert report.status is APMEEvaluationStatus.COMPLETED
        assert report.decisions == ()
        assert report.pipeline_summary.total_stages == len(STAGE_ORDER)

    def test_single_position_hold(self, engine: AdaptivePositionManagementEngine) -> None:
        position = make_position(expiry="2026-09-15", unrealized_pnl=150.0)
        snapshot = ingest_portfolio_snapshot(positions=(position,), fingerprint="pf-hold")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        assert report.status in (APMEEvaluationStatus.COMPLETED, APMEEvaluationStatus.PARTIAL)
        decision = report.decisions[0]
        assert decision.primary_action is ManagementAction.HOLD
        assert decision.exit_decision is not None
        assert decision.exit_decision.recommended is False

    def test_time_exit_dte_one(self, engine: AdaptivePositionManagementEngine) -> None:
        position = replace(
            make_position(expiry="2026-08-05", unrealized_pnl=-50.0),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "expiry": "2026-08-05",
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                    "position_group_id": "plan-1",
                }
            ),
        )
        snapshot = ingest_portfolio_snapshot(positions=(position,), fingerprint="pf-time-exit")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        decision = report.decisions[0]
        assert decision.primary_action is ManagementAction.FULL_EXIT
        assert decision.exit_decision is not None
        assert decision.exit_decision.recommended is True
        assert "APME.TIME.EXIT.DTE_THRESHOLD" in decision.exit_decision.reason_codes

    def test_stop_breach_full_exit(self, engine: AdaptivePositionManagementEngine) -> None:
        position = make_position(expiry="2026-08-28", unrealized_pnl=-200.0)
        snapshot = ingest_portfolio_snapshot(positions=(position,), fingerprint="pf-stop")
        position_id = snapshot.positions[0].position_id
        signal_meta = SignalManagementMetadata(
            position_id=position_id,
            stop_loss_hint=StopLossHint(
                hint_type=StopLossHintType.UNDERLYING_LEVEL,
                reference="NIFTY",
                value=25_000.0,
            ),
        )
        context = make_apme_context(
            snapshot,
            underlying_marks=MappingProxyType({"NIFTY": 25_100.0}),
            signal_metadata=MappingProxyType({position_id: signal_meta}),
        )
        report = engine.evaluate(snapshot, context)
        decision = report.decisions[0]
        assert decision.primary_action is ManagementAction.FULL_EXIT
        assert "APME.STOP.BREACH.UNDERLYING_LEVEL" in decision.exit_decision.reason_codes  # type: ignore[union-attr]

    def test_news_critical_full_exit(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-news")
        news = NewsEventFlag(
            event_id="news-rbi",
            event_type="RBI_POLICY",
            severity="CRITICAL",
            affected_underlyings=("NIFTY",),
            valid_from=fixed_as_of() - timedelta(hours=1),
        )
        report = engine.evaluate(snapshot, make_apme_context(snapshot, news_flags=(news,)))
        decision = report.decisions[0]
        assert decision.primary_action is ManagementAction.FULL_EXIT
        assert "APME.NEWS.EXIT.CRITICAL" in decision.exit_decision.reason_codes  # type: ignore[union-attr]

    def test_drawdown_protection_escalate(
        self, engine: AdaptivePositionManagementEngine
    ) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-drawdown")
        stressed_metrics = replace(
            snapshot.metrics,
            equity_hint=850_000.0,
            peak_equity_hint=1_000_000.0,
        )
        stressed = replace(
            snapshot,
            metrics=stressed_metrics,
            snapshot_fingerprint=compute_snapshot_fingerprint(
                replace(snapshot, metrics=stressed_metrics, snapshot_fingerprint="")
            ),
        )
        report = engine.evaluate(stressed, make_apme_context(stressed))
        assert report.portfolio_actions
        assert any(a.trigger_code == "APME.PORTFOLIO.DRAWDOWN.LIMIT" for a in report.portfolio_actions)
        decision = report.decisions[0]
        assert decision.primary_action is ManagementAction.ESCALATE
        assert report.escalations

    def test_vix_crisis_full_exit(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-vix")
        vol = VolatilityHints(
            as_of=fixed_as_of(),
            vix_regime="CRISIS",
            vix_level=38.0,
        )
        report = engine.evaluate(snapshot, make_apme_context(snapshot, volatility_hints=vol))
        decision = report.decisions[0]
        assert decision.primary_action is ManagementAction.FULL_EXIT
        assert "APME.VOL.EXIT.CRISIS_REGIME" in decision.exit_decision.reason_codes  # type: ignore[union-attr]

    def test_all_stages_recorded(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-stages")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        assert report.pipeline_summary.total_stages == 22
        assert len(report.pipeline_summary.stages) == 22
        stage_ids = {stage.stage_id for stage in report.pipeline_summary.stages}
        assert stage_ids == set(STAGE_ORDER)

    def test_idempotent_noop(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-idem")
        context = make_apme_context(snapshot)
        first = engine.evaluate(snapshot, context)
        second = engine.evaluate(snapshot, context)
        assert first.status in (APMEEvaluationStatus.COMPLETED, APMEEvaluationStatus.PARTIAL)
        assert second.status is APMEEvaluationStatus.NOOP
        assert second.report_fingerprint == first.report_fingerprint


class TestValidation:
    def test_validate_evaluation_context_valid(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-val-ctx")
        result = validate_evaluation_context(
            make_apme_context(snapshot),
            snapshot,
            apme_fast_config(),
        )
        assert result.is_valid

    def test_validate_evaluation_context_invalid_mode(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-val-mode")
        context = replace(make_apme_context(snapshot), execution_mode="invalid")  # type: ignore[arg-type]
        result = validate_evaluation_context(context, snapshot, apme_fast_config())
        assert not result.is_valid
        assert result.errors[0].code == ERROR_CONTEXT_INVALID

    def test_validate_apme_decision_report_valid(
        self, engine: AdaptivePositionManagementEngine
    ) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-val-report")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        validation = validate_apme_decision_report(report)
        assert validation.is_valid
        assert_valid_apme_decision_report(report)

    def test_validate_apme_decision_report_invalid(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-val-bad-report")
        engine = AdaptivePositionManagementEngine(apme_fast_config())
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        bad_report = replace(report, report_id="")
        validation = validate_apme_decision_report(bad_report)
        assert not validation.is_valid
        assert validation.errors[0].code == ERROR_RESULT_INVALID

    def test_assert_valid_raises(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-assert")
        engine = AdaptivePositionManagementEngine(apme_fast_config())
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        with pytest.raises(APMEValidationError):
            assert_valid_apme_decision_report(replace(report, duration_ms=-1.0))

    def test_engine_validate_helpers(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-eng-val")
        context = make_apme_context(snapshot)
        assert engine.validate_evaluation_context(context, snapshot).is_valid
        report = engine.evaluate(snapshot, context)
        assert engine.validate_report(report).is_valid


class TestDeterminism:
    def test_stable_report_fingerprint(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-det-fp")
        context = make_apme_context(snapshot)
        first_engine = AdaptivePositionManagementEngine(apme_fast_config())
        second_engine = AdaptivePositionManagementEngine(apme_fast_config())
        first = first_engine.evaluate(snapshot, context)
        second = second_engine.evaluate(snapshot, context)
        assert first.report_fingerprint == second.report_fingerprint
        recomputed = compute_report_fingerprint(snapshot, first, apme_fast_config())
        assert recomputed == first.report_fingerprint


class TestSerialization:
    def test_round_trip_decision_report(
        self, engine: AdaptivePositionManagementEngine
    ) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-ser-report")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        payload = serialize_apme_decision_report(report)
        restored = deserialize_apme_decision_report(payload)
        assert restored.report_id == report.report_id
        assert restored.status == report.status
        assert len(restored.decisions) == len(report.decisions)
        assert restored.report_fingerprint == report.report_fingerprint

    def test_round_trip_position_decision(
        self, engine: AdaptivePositionManagementEngine
    ) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-ser-decision")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        decision = report.decisions[0]
        payload = serialize_position_management_decision(decision)
        restored = deserialize_position_management_decision(payload)
        assert restored.decision_id == decision.decision_id
        assert restored.primary_action == decision.primary_action

    def test_malformed_json(self) -> None:
        with pytest.raises(APMEValidationError) as exc:
            deserialize_apme_decision_report("{bad")
        assert exc.value.code == ERROR_SERIALIZATION_MALFORMED

    def test_unsupported_schema_version(
        self, engine: AdaptivePositionManagementEngine
    ) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-schema")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        data = json.loads(serialize_apme_decision_report(report))
        data["schema_version"] = "9.9.9"
        with pytest.raises(APMEValidationError) as exc:
            deserialize_apme_decision_report(json.dumps(data))
        assert exc.value.code == ERROR_SERIALIZATION_UNSUPPORTED_VERSION

    def test_deserialize_non_object_payload(self) -> None:
        with pytest.raises(APMEValidationError) as exc:
            deserialize_position_management_decision("[]")
        assert exc.value.code == ERROR_SERIALIZATION_MALFORMED


class TestEvents:
    def test_lifecycle_events_published(self) -> None:
        bus = EventBus()
        captured: list[object] = []
        bus.subscribe("apme.*", lambda event: captured.append(event.payload))
        engine = AdaptivePositionManagementEngine(apme_fast_config(), event_bus=bus)
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-events")
        engine.evaluate(snapshot, make_apme_context(snapshot))
        topics = {getattr(payload, "topic", "") for payload in captured}
        assert "apme.evaluation.received" in topics
        assert "apme.evaluation.completed" in topics
        assert "apme.report.published" in topics

    def test_event_type_topics(self) -> None:
        for event_type in APMEEventType:
            assert event_type.topic.startswith("apme.")

    def test_on_portfolio_snapshot_event(self, engine: AdaptivePositionManagementEngine) -> None:
        event = PortfolioEvent(
            event_type=PortfolioEventType.SNAPSHOT_PUBLISHED,
            topic="portfolio.snapshot.published",
            update_id="upd-1",
            correlation_id="corr-1",
            occurred_at=fixed_as_of(),
        )
        engine.on_portfolio_snapshot_event(event)

    def test_on_non_snapshot_event_ignored(
        self, engine: AdaptivePositionManagementEngine
    ) -> None:
        event = PortfolioEvent(
            event_type=PortfolioEventType.INGEST_COMPLETED,
            topic="portfolio.ingest.completed",
            update_id="upd-1",
            correlation_id="corr-1",
            occurred_at=fixed_as_of(),
        )
        engine.on_portfolio_snapshot_event(event)

    def test_evaluate_on_portfolio_event(
        self, engine: AdaptivePositionManagementEngine
    ) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-port-event")
        event = PortfolioEvent(
            event_type=PortfolioEventType.SNAPSHOT_PUBLISHED,
            topic="portfolio.snapshot.published",
            update_id="upd-1",
            correlation_id="corr-1",
            occurred_at=fixed_as_of(),
            snapshot=snapshot,
        )
        report = engine.evaluate_on_portfolio_event(
            event,
            snapshot,
            make_apme_context(snapshot),
        )
        assert report.status in (APMEEvaluationStatus.COMPLETED, APMEEvaluationStatus.PARTIAL)

    def test_health_degraded_event(self) -> None:
        bus = EventBus()
        captured: list[object] = []
        bus.subscribe("apme.health.degraded", lambda event: captured.append(event.payload))
        engine = AdaptivePositionManagementEngine(apme_fast_config(), event_bus=bus)
        healthy = make_position(position_id="pos-degrade", expiry="2026-09-15", unrealized_pnl=200.0)
        snapshot1 = ingest_portfolio_snapshot(positions=(healthy,), fingerprint="pf-health-1")
        engine.evaluate(snapshot1, make_apme_context(snapshot1))
        stressed = replace(
            make_position(position_id="pos-degrade", expiry="2026-08-05", unrealized_pnl=-300.0),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "expiry": "2026-08-05",
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                    "position_group_id": "plan-1",
                    "short_strike_distance_pct": "0.2",
                }
            ),
        )
        snapshot2 = ingest_portfolio_snapshot(positions=(stressed,), fingerprint="pf-health-2")
        report = engine.evaluate(snapshot2, make_apme_context(snapshot2))
        assert report.decisions[0].health.health_status in (
            HealthStatus.STRESSED,
            HealthStatus.CRITICAL,
        )
        assert captured


class TestQueryApi:
    def test_get_latest_report_and_position_decision(
        self, engine: AdaptivePositionManagementEngine
    ) -> None:
        assert engine.get_latest_report() is None
        assert engine.get_position_decision("pos-1") is None
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-query")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        assert engine.get_latest_report() is report
        position_id = report.decisions[0].position_id
        assert engine.get_position_decision(position_id) is report.decisions[0]
        assert engine.get_position_decision("missing") is None

    def test_engine_config_property(self, engine: AdaptivePositionManagementEngine) -> None:
        assert engine.config.decision_cooldown_seconds == 0


class TestThreadSafety:
    def test_concurrent_evaluate(self) -> None:
        engine = AdaptivePositionManagementEngine(apme_fast_config())
        lock = threading.Lock()
        errors: list[Exception] = []

        def evaluate_one(index: int) -> None:
            try:
                position = make_position(
                    position_id=f"pos-{index}",
                    instrument_key=f"NFO:INST{index}CE",
                )
                snapshot = ingest_portfolio_snapshot(
                    positions=(position,),
                    fingerprint=f"pf-thread-{index}",
                )
                context = make_apme_context(
                    snapshot,
                    price_hints=MappingProxyType({f"NFO:INST{index}CE": 130.0}),
                )
                engine.evaluate(snapshot, context)
            except Exception as exc:  # pragma: no cover - collected for assertion
                with lock:
                    errors.append(exc)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(evaluate_one, index) for index in range(4)]
            for future in futures:
                future.result()

        assert not errors
        assert engine.get_latest_report() is not None


class TestAdditionalCoverage:
    def test_session_cutoff_time_exit(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-session")
        session = SessionContext(
            session_date="2026-08-04",
            minutes_to_close=15,
        )
        report = engine.evaluate(
            snapshot,
            make_apme_context(snapshot, session_context=session),
        )
        decision = report.decisions[0]
        assert decision.exit_decision is not None
        assert "APME.TIME.EXIT.SESSION_CUTOFF" in decision.exit_decision.reason_codes

    def test_trend_reversal_partial_exit(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-trend")
        trend = TrendHints(
            underlying="NIFTY",
            as_of=fixed_as_of(),
            trend_direction="UP",
            trend_strength=0.8,
            reversal_detected=True,
        )
        report = engine.evaluate(
            snapshot,
            make_apme_context(snapshot, trend_hints=MappingProxyType({"NIFTY": trend})),
        )
        decision = report.decisions[0]
        assert decision.primary_action in (
            ManagementAction.PARTIAL_EXIT,
            ManagementAction.FULL_EXIT,
        )

    def test_stale_greek_hint_warning(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-stale-greek")
        position_id = snapshot.positions[0].position_id
        stale = PositionGreekHint(
            position_id=position_id,
            as_of=fixed_as_of() - timedelta(minutes=30),
            delta=0.1,
        )
        engine = AdaptivePositionManagementEngine(apme_fast_config(hint_max_age_seconds=60))
        report = engine.evaluate(
            snapshot,
            make_apme_context(snapshot, greek_hints=MappingProxyType({position_id: stale})),
        )
        from apme.adaptive_position_management_engine import WARN_HINT_STALE

        assert any(w.code == WARN_HINT_STALE for w in report.warnings)

    def test_drawdown_reduce_threshold(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-drawdown-reduce")
        metrics = replace(
            snapshot.metrics,
            equity_hint=940_000.0,
            peak_equity_hint=1_000_000.0,
        )
        adjusted = replace(
            snapshot,
            metrics=metrics,
            snapshot_fingerprint=compute_snapshot_fingerprint(
                replace(snapshot, metrics=metrics, snapshot_fingerprint="")
            ),
        )
        report = engine.evaluate(adjusted, make_apme_context(adjusted))
        assert any(a.trigger_code == "APME.PORTFOLIO.DRAWDOWN.REDUCE" for a in report.portfolio_actions)

    def test_profit_protection_on_max_profit(self, engine: AdaptivePositionManagementEngine) -> None:
        position = replace(
            make_position(unrealized_pnl=600.0),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "expiry": "2026-08-28",
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                    "position_group_id": "plan-1",
                    "max_profit": "1000",
                }
            ),
        )
        snapshot = ingest_portfolio_snapshot(positions=(position,), fingerprint="pf-profit")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        decision = report.decisions[0]
        assert decision.profit_protection_decision is not None
        assert decision.profit_protection_decision.recommended is True

    def test_adjustment_delta_drift(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-adjust")
        position_id = snapshot.positions[0].position_id
        greek = PositionGreekHint(
            position_id=position_id,
            as_of=fixed_as_of(),
            delta=0.40,
        )
        report = engine.evaluate(
            snapshot,
            make_apme_context(snapshot, greek_hints=MappingProxyType({position_id: greek})),
        )
        decision = report.decisions[0]
        assert decision.adjustment_decision is not None
        assert decision.adjustment_decision.recommended is True

    def test_group_decision_for_multi_leg(self, engine: AdaptivePositionManagementEngine) -> None:
        leg_meta = {
            "underlying": "NIFTY",
            "expiry": "2026-08-28",
            "opened_at": "2026-08-04T04:30:00.000Z",
            "correlation_id": "corr-1",
            "position_group_id": "plan-1",
        }
        ce = replace(
            make_position(position_id="ce", instrument_key="NFO:NIFTY24AUG25000CE"),
            metadata=MappingProxyType(leg_meta),
        )
        pe = replace(
            make_position(
                position_id="pe",
                instrument_key="NFO:NIFTY24AUG25000PE",
                average_entry_price=118.25,
            ),
            metadata=MappingProxyType(leg_meta),
        )
        snapshot = ingest_portfolio_snapshot(positions=(ce, pe), fingerprint="pf-group")
        context = make_apme_context(
            snapshot,
            price_hints=MappingProxyType(
                {
                    "NFO:NIFTY24AUG25000CE": 130.0,
                    "NFO:NIFTY24AUG25000PE": 118.0,
                }
            ),
        )
        report = engine.evaluate(snapshot, context)
        assert report.group_decisions
        assert len(report.decisions) == 2

    def test_non_deterministic_report_id(self) -> None:
        engine = AdaptivePositionManagementEngine(
            apme_fast_config(deterministic_fingerprint=False)
        )
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-nondet")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        assert report.report_id.startswith("apme-")


class TestEdgeCaseEngines:
    def test_elevated_vol_partial_exit(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-vol-elevated")
        vol = VolatilityHints(as_of=fixed_as_of(), vix_regime="ELEVATED")
        report = engine.evaluate(snapshot, make_apme_context(snapshot, volatility_hints=vol))
        decision = report.decisions[0]
        assert decision.exit_decision is not None
        assert "APME.VOL.EXIT.ELEVATED_REGIME" in decision.exit_decision.reason_codes

    def test_news_high_severity_partial_exit(
        self, engine: AdaptivePositionManagementEngine
    ) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-news-high")
        news = NewsEventFlag(
            event_id="news-high",
            event_type="EARNINGS",
            severity="HIGH",
            affected_underlyings=("NIFTY",),
            valid_from=fixed_as_of() - timedelta(hours=1),
        )
        report = engine.evaluate(snapshot, make_apme_context(snapshot, news_flags=(news,)))
        assert "APME.NEWS.EXIT.HIGH_SEVERITY" in report.decisions[0].exit_decision.reason_codes  # type: ignore[union-attr]

    def test_news_medium_severity(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-news-med")
        news = NewsEventFlag(
            event_id="news-med",
            event_type="MACRO",
            severity="MEDIUM",
            affected_underlyings=("NIFTY",),
            valid_from=fixed_as_of() - timedelta(hours=1),
        )
        report = engine.evaluate(snapshot, make_apme_context(snapshot, news_flags=(news,)))
        assert "APME.NEWS.EXIT.MEDIUM_SEVERITY" in report.decisions[0].exit_decision.reason_codes  # type: ignore[union-attr]

    def test_news_expired_flag_ignored(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-news-expired")
        news = NewsEventFlag(
            event_id="news-old",
            event_type="MACRO",
            severity="CRITICAL",
            affected_underlyings=("NIFTY",),
            valid_from=fixed_as_of() - timedelta(days=2),
            valid_until=fixed_as_of() - timedelta(days=1),
        )
        report = engine.evaluate(snapshot, make_apme_context(snapshot, news_flags=(news,)))
        assert report.decisions[0].primary_action is ManagementAction.HOLD

    def test_premium_multiple_stop_breach(self, engine: AdaptivePositionManagementEngine) -> None:
        position = replace(
            make_position(unrealized_pnl=-500.0),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "expiry": "2026-08-28",
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                    "position_group_id": "plan-1",
                    "entry_premium": "100",
                }
            ),
        )
        snapshot = ingest_portfolio_snapshot(positions=(position,), fingerprint="pf-prem-stop")
        position_id = snapshot.positions[0].position_id
        signal_meta = SignalManagementMetadata(
            position_id=position_id,
            stop_loss_hint=StopLossHint(
                hint_type=StopLossHintType.PREMIUM_MULTIPLE,
                reference="premium",
                value=2.0,
            ),
        )
        report = engine.evaluate(
            snapshot,
            make_apme_context(
                snapshot,
                signal_metadata=MappingProxyType({position_id: signal_meta}),
            ),
        )
        assert report.decisions[0].primary_action is ManagementAction.FULL_EXIT

    def test_structure_breach_stop(self, engine: AdaptivePositionManagementEngine) -> None:
        position = replace(
            make_position(unrealized_pnl=-100.0),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "expiry": "2026-08-28",
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                    "position_group_id": "plan-1",
                    "short_strike_distance_pct": "0.05",
                }
            ),
        )
        snapshot = ingest_portfolio_snapshot(positions=(position,), fingerprint="pf-struct-stop")
        position_id = snapshot.positions[0].position_id
        signal_meta = SignalManagementMetadata(
            position_id=position_id,
            stop_loss_hint=StopLossHint(
                hint_type=StopLossHintType.STRUCTURE_BREACH,
                reference="structure",
            ),
        )
        report = engine.evaluate(
            snapshot,
            make_apme_context(
                snapshot,
                signal_metadata=MappingProxyType({position_id: signal_meta}),
            ),
        )
        assert report.decisions[0].primary_action is ManagementAction.FULL_EXIT

    def test_max_hold_time_exit(self, engine: AdaptivePositionManagementEngine) -> None:
        position = replace(
            make_position(),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "expiry": "2026-08-28",
                    "opened_at": "2026-08-04T02:00:00.000Z",
                    "correlation_id": "corr-1",
                    "position_group_id": "plan-1",
                }
            ),
        )
        snapshot = ingest_portfolio_snapshot(positions=(position,), fingerprint="pf-max-hold")
        position_id = snapshot.positions[0].position_id
        signal_meta = SignalManagementMetadata(
            position_id=position_id,
            max_hold_minutes=30,
        )
        report = engine.evaluate(
            snapshot,
            make_apme_context(
                snapshot,
                signal_metadata=MappingProxyType({position_id: signal_meta}),
            ),
        )
        assert "APME.TIME.EXIT.MAX_HOLD" in report.decisions[0].exit_decision.reason_codes  # type: ignore[union-attr]

    def test_break_even_profit_protection(self) -> None:
        from apme.adaptive_position_management_engine import (
            _engine_break_even,
            _engine_profit_protection,
        )

        position = replace(
            make_position(unrealized_pnl=50.0),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "expiry": "2026-08-28",
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                    "position_group_id": "plan-1",
                    "was_unprofitable": "true",
                }
            ),
        )
        snapshot = ingest_portfolio_snapshot(positions=(position,), fingerprint="pf-breakeven")
        ctx = make_position_context(snapshot)
        assert _engine_break_even(ctx) is True
        pp = _engine_profit_protection(ctx, apme_fast_config(), break_even_crossed=True)
        assert pp.recommended is True

    def test_roll_near_expiry_profitable(self, engine: AdaptivePositionManagementEngine) -> None:
        position = replace(
            make_position(expiry="2026-08-05", unrealized_pnl=100.0),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "expiry": "2026-08-05",
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                    "position_group_id": "plan-1",
                }
            ),
        )
        snapshot = ingest_portfolio_snapshot(positions=(position,), fingerprint="pf-roll")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        decision = report.decisions[0]
        assert decision.roll_decision is not None
        assert decision.roll_decision.recommended is True

    def test_wing_stress_adjustment(self, engine: AdaptivePositionManagementEngine) -> None:
        position = replace(
            make_position(unrealized_pnl=-50.0),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "expiry": "2026-08-28",
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                    "position_group_id": "plan-1",
                    "short_strike_distance_pct": "0.4",
                }
            ),
        )
        snapshot = ingest_portfolio_snapshot(positions=(position,), fingerprint="pf-wing")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        assert report.decisions[0].adjustment_decision is not None
        assert report.decisions[0].adjustment_decision.recommended is True

    def test_margin_stress_portfolio_protection(
        self, engine: AdaptivePositionManagementEngine
    ) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-margin-stress")
        metrics = replace(
            snapshot.metrics,
            margin_utilization_pct=90.0,
        )
        adjusted = replace(
            snapshot,
            metrics=metrics,
            snapshot_fingerprint=compute_snapshot_fingerprint(
                replace(snapshot, metrics=metrics, snapshot_fingerprint="")
            ),
        )
        report = engine.evaluate(adjusted, make_apme_context(adjusted))
        assert any(a.trigger_code == "APME.PORTFOLIO.MARGIN.STRESS" for a in report.portfolio_actions)

    def test_tail_risk_hedge(self, engine: AdaptivePositionManagementEngine) -> None:
        position = replace(
            make_position(),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "expiry": "2026-08-28",
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                    "position_group_id": "plan-1",
                    "tail_risk_score": "0.9",
                }
            ),
        )
        snapshot = ingest_portfolio_snapshot(positions=(position,), fingerprint="pf-tail")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        hedge = report.decisions[0].hedge_decision
        assert hedge is not None
        assert hedge.recommended is True

    def test_monitor_on_stressed_health(self, engine: AdaptivePositionManagementEngine) -> None:
        position = replace(
            make_position(unrealized_pnl=-50.0),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "expiry": "2026-08-28",
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                    "position_group_id": "plan-1",
                    "short_strike_distance_pct": "0.45",
                    "bid_ask_spread_pct": "8",
                }
            ),
        )
        snapshot = ingest_portfolio_snapshot(positions=(position,), fingerprint="pf-monitor")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        assert report.decisions[0].primary_action in (
            ManagementAction.MONITOR,
            ManagementAction.ADJUST,
            ManagementAction.HOLD,
        )

    def test_require_signal_metadata_warning(self) -> None:
        engine = AdaptivePositionManagementEngine(apme_fast_config(require_signal_metadata=True))
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-req-signal")
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        from apme.adaptive_position_management_engine import WARN_SIGNAL_METADATA_MISSING

        assert any(w.code == WARN_SIGNAL_METADATA_MISSING for w in report.warnings)

    def test_stale_volatility_hint_warning(self) -> None:
        engine = AdaptivePositionManagementEngine(apme_fast_config(hint_max_age_seconds=60))
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-stale-vol")
        vol = VolatilityHints(
            as_of=fixed_as_of() - timedelta(minutes=30),
            vix_regime="NORMAL",
        )
        report = engine.evaluate(snapshot, make_apme_context(snapshot, volatility_hints=vol))
        from apme.adaptive_position_management_engine import WARN_HINT_STALE

        assert any(w.code == WARN_HINT_STALE for w in report.warnings)

    def test_health_score_invalid_dte_metadata(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-bad-dte")
        ctx = make_position_context(snapshot, metadata_extra={"dte": "bad"})
        health = compute_position_health(ctx, apme_fast_config(), reference_time=fixed_as_of())
        assert health.health_score > 0

    def test_health_invalid_spread_hint(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-bad-spread")
        ctx = make_position_context(snapshot, metadata_extra={"bid_ask_spread_pct": "bad"})
        health = compute_position_health(ctx, apme_fast_config(), reference_time=fixed_as_of())
        assert health.liquidity_score > 0

    def test_health_invalid_max_loss(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-bad-maxloss")
        ctx = make_position_context(
            snapshot,
            metadata_extra={"max_loss": "bad"},
        )
        health = compute_position_health(ctx, apme_fast_config(), reference_time=fixed_as_of())
        assert health.pnl_health_score > 0

    def test_quality_band_excellent(self) -> None:
        from apme.adaptive_position_management_engine import _quality_band, QualityScoreBand

        assert _quality_band(0.85) is QualityScoreBand.EXCELLENT
        assert _quality_band(0.65) is QualityScoreBand.GOOD
        assert _quality_band(0.45) is QualityScoreBand.FAIR
        assert _quality_band(0.25) is QualityScoreBand.POOR
        assert _quality_band(0.10) is QualityScoreBand.CRITICAL

    def test_health_status_from_score_edges(self) -> None:
        from apme.adaptive_position_management_engine import _health_status_from_score

        assert _health_status_from_score(0.0) is HealthStatus.UNKNOWN
        assert _health_status_from_score(0.80) is HealthStatus.HEALTHY

    def test_validate_duplicate_decision_ids(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-dup-decision")
        engine = AdaptivePositionManagementEngine(apme_fast_config())
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        dup = replace(report.decisions[0], decision_id="dup-id")
        bad_report = replace(report, decisions=(dup, dup))
        validation = validate_apme_decision_report(bad_report)
        assert not validation.is_valid

    def test_validate_non_hold_missing_explainability(self) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-no-expl")
        engine = AdaptivePositionManagementEngine(apme_fast_config())
        report = engine.evaluate(snapshot, make_apme_context(snapshot))
        bad_decision = replace(
            report.decisions[0],
            primary_action=ManagementAction.FULL_EXIT,
            explainability=(),
        )
        bad_report = replace(report, decisions=(bad_decision,))
        validation = validate_apme_decision_report(bad_report)
        assert not validation.is_valid

    def test_news_exit_hooks_disabled(self) -> None:
        engine = AdaptivePositionManagementEngine(apme_fast_config(enable_news_exit_hooks=False))
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-no-news")
        news = NewsEventFlag(
            event_id="news-off",
            event_type="MACRO",
            severity="CRITICAL",
            affected_underlyings=("NIFTY",),
            valid_from=fixed_as_of() - timedelta(hours=1),
        )
        report = engine.evaluate(snapshot, make_apme_context(snapshot, news_flags=(news,)))
        assert report.decisions[0].primary_action is ManagementAction.HOLD

    def test_portfolio_protection_disabled(self) -> None:
        engine = AdaptivePositionManagementEngine(apme_fast_config(enable_portfolio_protection=False))
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-no-protect")
        metrics = replace(snapshot.metrics, equity_hint=850_000.0, peak_equity_hint=1_000_000.0)
        adjusted = replace(
            snapshot,
            metrics=metrics,
            snapshot_fingerprint=compute_snapshot_fingerprint(
                replace(snapshot, metrics=metrics, snapshot_fingerprint="")
            ),
        )
        report = engine.evaluate(adjusted, make_apme_context(adjusted))
        assert not report.portfolio_actions

    def test_risk_escalation_disabled(self) -> None:
        engine = AdaptivePositionManagementEngine(apme_fast_config(enable_risk_escalation=False))
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-no-escalate")
        metrics = replace(snapshot.metrics, equity_hint=850_000.0, peak_equity_hint=1_000_000.0)
        adjusted = replace(
            snapshot,
            metrics=metrics,
            snapshot_fingerprint=compute_snapshot_fingerprint(
                replace(snapshot, metrics=metrics, snapshot_fingerprint="")
            ),
        )
        report = engine.evaluate(adjusted, make_apme_context(adjusted))
        assert not report.escalations

    def test_missing_snapshot_fingerprint_warning(
        self, engine: AdaptivePositionManagementEngine
    ) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-no-fp")
        no_fp = replace(snapshot, snapshot_fingerprint="")
        report = engine.evaluate(no_fp, make_apme_context(no_fp))
        assert any("fingerprint missing" in w.message for w in report.warnings)

    def test_empty_snapshot_id_integrity(self, engine: AdaptivePositionManagementEngine) -> None:
        snapshot = ingest_portfolio_snapshot(fingerprint="pf-no-snap-id")
        bad = replace(snapshot, snapshot_id="")
        report = engine.evaluate(bad, make_apme_context(bad))
        integrity = next(
            s for s in report.pipeline_summary.stages
            if s.stage_id is APMEEvaluationStageId.SNAPSHOT_INTEGRITY
        )
        assert integrity.rejection_code == ERROR_SNAPSHOT_INVALID

    def test_deserialize_position_decision_malformed(self) -> None:
        with pytest.raises(APMEValidationError) as exc:
            deserialize_position_management_decision("{")
        assert exc.value.code == ERROR_SERIALIZATION_MALFORMED
