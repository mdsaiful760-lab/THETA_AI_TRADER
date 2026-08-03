"""Unit tests for portfolio.portfolio_manager."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from core.event_bus import EventBus
from execution.execution_engine import ProductType
from portfolio.portfolio_manager import (
    ERROR_ACCOUNT_INVALID_EQUITY,
    ERROR_CONTEXT_CORRELATION_MISMATCH,
    ERROR_CONTEXT_INVALID,
    ERROR_CONTEXT_MISSING_ACCOUNT,
    ERROR_CONTEXT_NAIVE_TIMESTAMP,
    ERROR_RESULT_INVALID,
    ERROR_SERIALIZATION_MALFORMED,
    ERROR_SERIALIZATION_UNSUPPORTED_VERSION,
    ERROR_SNAPSHOT_INVALID,
    ERROR_SNAPSHOT_MISSING,
    PortfolioAggregationBucket,
    PortfolioIngestContext,
    PortfolioManager,
    PortfolioManagerConfig,
    PortfolioManagerConfigurationError,
    PortfolioManagerValidationError,
    PortfolioUpdateStatus,
    PositionGreekHint,
    WARN_EXPIRY_UNRESOLVED,
    WARN_GREEK_HINT_MISSING,
    WARN_GREEK_HINT_STALE,
    WARN_MARGIN_HINT_MISSING,
    WARN_MARGIN_HINT_STALE,
    WARN_PNL_MISMATCH,
    WARN_PRICE_MARK_MISSING,
    attach_greek_hints,
    aggregate_portfolio_greeks,
    assert_valid_portfolio_update_result,
    compute_capital_utilization_pct,
    compute_gross_notional,
    compute_margin_utilization_pct,
    compute_notional_exposure,
    compute_total_unrealized_pnl,
    compute_update_fingerprint,
    default_portfolio_manager_config,
    deserialize_portfolio_snapshot,
    deserialize_portfolio_update_result,
    map_position_to_summary,
    serialize_portfolio_snapshot,
    serialize_portfolio_update_result,
    validate_ingest_context,
    validate_portfolio_update_result,
)
from portfolio.position_manager import (
    Position,
    PositionEvent,
    PositionEventType,
    PositionLifecycleState,
    PositionPipelineResult,
    PositionSide,
    PositionSnapshot,
    PositionStageResult,
    PositionUpdateResult,
    PositionUpdateStageId,
    PositionUpdateStatus,
)
from strategy.signals import StrategyExecutionMode, StrategyFamily

IST = ZoneInfo("Asia/Kolkata")


def fixed_as_of() -> datetime:
    """Return fixed timezone-aware reference time."""
    return datetime(2026, 8, 4, 10, 0, 0, tzinfo=IST)


def fast_config(**overrides: object) -> PortfolioManagerConfig:
    """Build fast deterministic test configuration."""
    base = default_portfolio_manager_config()
    defaults = {"require_account_hints": False}
    defaults.update(overrides)
    return replace(base, **defaults)


def make_position(
    *,
    position_id: str = "pos-1",
    instrument_key: str = "NFO:NIFTY24AUG25000CE",
    side: PositionSide = PositionSide.SHORT,
    quantity: int = 75,
    average_entry_price: float = 125.50,
    unrealized_pnl: float = 100.0,
    realized_pnl: float = 0.0,
    strategy_id: str = "short-strangle-v1",
    underlying: str = "NIFTY",
    expiry: str = "2026-08-28",
) -> Position:
    """Build position fixture."""
    return Position(
        position_id=position_id,
        instrument_key=instrument_key,
        side=side,
        product=ProductType.NRML,
        quantity=quantity,
        average_entry_price=average_entry_price,
        cost_basis=round(average_entry_price * quantity, 2),
        lifecycle_state=PositionLifecycleState.OPEN,
        strategy_id=strategy_id,
        strategy_family=StrategyFamily.SHORT_STRANGLE,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        transitions=(),
        metadata=MappingProxyType(
            {
                "underlying": underlying,
                "expiry": expiry,
                "opened_at": "2026-08-04T04:30:00.000Z",
                "correlation_id": "corr-1",
            }
        ),
        position_group_id="plan-1",
    )


def make_position_snapshot(
    *positions: Position,
    snapshot_id: str = "psnap-1",
    fingerprint: str = "pf-pos-1",
) -> PositionSnapshot:
    """Build position snapshot fixture."""
    pos = positions or (make_position(),)
    underlying_map: dict[str, int] = {}
    unrealized = 0.0
    for position in pos:
        underlying = position.metadata.get("underlying", "NIFTY")
        underlying_map[underlying] = underlying_map.get(underlying, 0) + position.quantity
        unrealized += position.unrealized_pnl
    return PositionSnapshot(
        snapshot_id=snapshot_id,
        as_of=fixed_as_of(),
        account_id="acct-1",
        positions=tuple(pos),
        open_position_count=len(pos),
        aggregate_quantity_by_underlying=MappingProxyType(dict(sorted(underlying_map.items()))),
        aggregate_unrealized_pnl=round(unrealized, 2),
        aggregate_realized_pnl_session=0.0,
        snapshot_fingerprint=fingerprint,
    )


def make_ingest_context(
    *,
    correlation_id: str = "corr-1",
    reference_time: datetime | None = None,
    execution_mode: StrategyExecutionMode = StrategyExecutionMode.BACKTEST,
    equity_hint: float = 1_000_000.0,
    account_id: str = "acct-1",
    price_hints: MappingProxyType | None = None,
    greek_hints: MappingProxyType | None = None,
    margin_available_hint: float | None = 500_000.0,
) -> PortfolioIngestContext:
    """Build portfolio ingest context."""
    return PortfolioIngestContext(
        correlation_id=correlation_id,
        reference_time=reference_time or fixed_as_of(),
        execution_mode=execution_mode,
        account_id=account_id,
        equity_hint=equity_hint,
        cash_available_hint=equity_hint * 0.5,
        margin_used_hint=100_000.0,
        margin_available_hint=margin_available_hint,
        greek_hints=greek_hints or MappingProxyType({}),
        price_hints=price_hints or MappingProxyType({}),
        tags=MappingProxyType({}),
    )


def make_position_update_result(snapshot: PositionSnapshot) -> PositionUpdateResult:
    """Build minimal position update result wrapper."""
    return PositionUpdateResult(
        update_id="pupd-1",
        tracker_submission_id="sub-1",
        correlation_id="corr-1",
        status=PositionUpdateStatus.APPLIED,
        snapshot=snapshot,
        updated_positions=snapshot.positions,
        pipeline_summary=PositionPipelineResult(
            total_stages=9,
            passed_stages=9,
            failed_stage_id=None,
            stages=(),
            short_circuited=False,
        ),
        warnings=(),
        errors=(),
        primary_error_code=None,
        submitted_at=fixed_as_of(),
        completed_at=fixed_as_of(),
        duration_ms=1.0,
        update_fingerprint="fp",
    )


@pytest.fixture
def manager() -> PortfolioManager:
    return PortfolioManager(fast_config())


class TestConfiguration:
    def test_invalid_margin_hint_max_age(self) -> None:
        with pytest.raises(PortfolioManagerConfigurationError):
            PortfolioManagerConfig(margin_hint_max_age_seconds=-1)

    def test_invalid_greek_hint_max_age(self) -> None:
        with pytest.raises(PortfolioManagerConfigurationError):
            PortfolioManagerConfig(greek_hint_max_age_seconds=-1)

    def test_default_config_factory(self) -> None:
        config = default_portfolio_manager_config()
        assert config.strict_correlation is True
        assert config.idempotent_ingest is True


class TestHelperFunctions:
    def test_notional_with_mark(self) -> None:
        assert compute_notional_exposure(75, mark_price=130.0, average_entry_price=125.0) == 9750.0

    def test_notional_fallback_avg(self) -> None:
        assert compute_notional_exposure(10, mark_price=None, average_entry_price=100.0) == 1000.0

    def test_gross_notional(self) -> None:
        summary = map_position_to_summary(
            make_position(),
            mark_price=130.0,
            greek_hint=None,
            config=fast_config(),
        )
        assert compute_gross_notional((summary,)) == summary.notional_exposure

    def test_unrealized_rollup(self) -> None:
        summary = map_position_to_summary(
            make_position(unrealized_pnl=50.0),
            mark_price=130.0,
            greek_hint=None,
            config=fast_config(),
        )
        assert compute_total_unrealized_pnl((summary,)) == 50.0

    def test_aggregate_greeks(self) -> None:
        s1 = map_position_to_summary(
            make_position(position_id="p1"),
            mark_price=130.0,
            greek_hint=PositionGreekHint(
                position_id="p1",
                as_of=fixed_as_of(),
                delta=0.5,
                gamma=0.01,
            ),
            config=fast_config(),
        )
        s2 = map_position_to_summary(
            make_position(position_id="p2", instrument_key="NFO:NIFTY24AUG25000PE"),
            mark_price=118.0,
            greek_hint=PositionGreekHint(
                position_id="p2",
                as_of=fixed_as_of(),
                delta=-0.3,
            ),
            config=fast_config(),
        )
        delta, gamma, theta, vega = aggregate_portfolio_greeks((s1, s2))
        assert delta == 0.2
        assert gamma == 0.01
        assert theta is None
        assert vega is None

    def test_capital_utilization(self) -> None:
        assert compute_capital_utilization_pct(25000.0, 1_000_000.0) == 2.5

    def test_margin_utilization(self) -> None:
        assert compute_margin_utilization_pct(100_000.0, 400_000.0) == 20.0

    def test_margin_utilization_none(self) -> None:
        assert compute_margin_utilization_pct(100_000.0, None) is None

    def test_margin_utilization_zero_denominator(self) -> None:
        assert compute_margin_utilization_pct(0.0, 0.0) is None

    def test_capital_utilization_zero_equity(self) -> None:
        assert compute_capital_utilization_pct(1000.0, 0.0) == 0.0

    def test_attach_greek_hints_no_match(self) -> None:
        summary = map_position_to_summary(
            make_position(position_id="missing-hint"),
            mark_price=130.0,
            greek_hint=None,
            config=fast_config(),
        )
        assert attach_greek_hints(summary, MappingProxyType({})) is summary

    def test_aggregate_theta_vega(self) -> None:
        s1 = map_position_to_summary(
            make_position(position_id="t1"),
            mark_price=130.0,
            greek_hint=PositionGreekHint(
                position_id="t1",
                as_of=fixed_as_of(),
                theta=-0.05,
                vega=0.12,
            ),
            config=fast_config(),
        )
        _, _, theta, vega = aggregate_portfolio_greeks((s1,))
        assert theta == -0.05
        assert vega == 0.12

    def test_map_instrument_expiry_metadata(self) -> None:
        position = replace(
            make_position(),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "instrument_expiry": "2026-09-15",
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                }
            ),
        )
        summary = map_position_to_summary(
            position,
            mark_price=130.0,
            greek_hint=None,
            config=fast_config(),
        )
        assert summary.expiry == "2026-09-15"

    def test_map_underlying_from_instrument_key(self) -> None:
        position = replace(
            make_position(instrument_key="NFO:BANKNIFTY24AUG52000CE"),
            metadata=MappingProxyType(
                {
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                }
            ),
        )
        summary = map_position_to_summary(
            position,
            mark_price=130.0,
            greek_hint=None,
            config=fast_config(),
        )
        assert summary.underlying == "NFO"

    def test_parse_invalid_opened_at(self) -> None:
        position = replace(
            make_position(),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "expiry": "2026-08-28",
                    "opened_at": "not-a-valid-date",
                    "correlation_id": "corr-1",
                }
            ),
        )
        summary = map_position_to_summary(
            position,
            mark_price=130.0,
            greek_hint=None,
            config=fast_config(),
        )
        assert summary.opened_at is None


class TestInputGate:
    def test_reject_naive_timestamp(self, manager: PortfolioManager) -> None:
        snapshot = make_position_snapshot()
        naive = datetime(2026, 8, 4, 10, 0, 0)
        context = make_ingest_context(reference_time=naive)
        result = manager.ingest_position_snapshot(snapshot, context)
        assert result.status is PortfolioUpdateStatus.REJECTED
        assert result.primary_error_code == ERROR_CONTEXT_NAIVE_TIMESTAMP

    def test_reject_correlation_mismatch_on_update_result(self, manager: PortfolioManager) -> None:
        snapshot = make_position_snapshot()
        pos_result = make_position_update_result(snapshot)
        context = make_ingest_context(correlation_id="other")
        result = manager.ingest_position_update_result(pos_result, context)
        assert result.status is PortfolioUpdateStatus.REJECTED
        assert result.primary_error_code == ERROR_CONTEXT_CORRELATION_MISMATCH

    def test_reject_invalid_equity_live(self) -> None:
        mgr = PortfolioManager(fast_config(require_account_hints=True))
        snapshot = make_position_snapshot()
        context = make_ingest_context(
            execution_mode=StrategyExecutionMode.LIVE,
            equity_hint=0.0,
        )
        result = mgr.ingest_position_snapshot(snapshot, context)
        assert result.status is PortfolioUpdateStatus.REJECTED
        assert result.primary_error_code == ERROR_ACCOUNT_INVALID_EQUITY

    def test_reject_missing_account_live(self) -> None:
        mgr = PortfolioManager(fast_config(require_account_hints=True))
        snapshot = make_position_snapshot()
        context = replace(
            make_ingest_context(execution_mode=StrategyExecutionMode.LIVE),
            account_id="",
        )
        result = mgr.ingest_position_snapshot(snapshot, context)
        assert result.status is PortfolioUpdateStatus.REJECTED
        assert result.primary_error_code == ERROR_CONTEXT_MISSING_ACCOUNT

    def test_reject_empty_correlation_id(self) -> None:
        snapshot = make_position_snapshot(fingerprint="pf-corr")
        context = replace(make_ingest_context(), correlation_id="")
        result = PortfolioManager(fast_config()).ingest_position_snapshot(snapshot, context)
        assert result.status is PortfolioUpdateStatus.REJECTED
        assert result.primary_error_code == ERROR_CONTEXT_CORRELATION_MISMATCH


class TestIngestPipeline:
    def test_single_position_ingest(self, manager: PortfolioManager) -> None:
        snapshot = make_position_snapshot()
        context = make_ingest_context(
            price_hints=MappingProxyType({"NFO:NIFTY24AUG25000CE": 130.0}),
        )
        result = manager.ingest_position_snapshot(snapshot, context)
        assert result.status is PortfolioUpdateStatus.APPLIED
        assert result.metrics.open_position_count == 1
        assert result.exposure.gross_notional > 0
        assert result.snapshot.positions[0].underlying == "NIFTY"

    def test_two_leg_portfolio(self, manager: PortfolioManager) -> None:
        ce = make_position(position_id="ce", instrument_key="NFO:NIFTY24AUG25000CE")
        pe = make_position(
            position_id="pe",
            instrument_key="NFO:NIFTY24AUG25000PE",
            average_entry_price=118.25,
        )
        snapshot = make_position_snapshot(ce, pe, fingerprint="pf-2")
        result = manager.ingest_position_snapshot(snapshot, make_ingest_context())
        assert result.metrics.open_position_count == 2
        assert len(result.snapshot.by_underlying) >= 1
        assert "short-strangle-v1" in result.snapshot.by_strategy

    def test_empty_portfolio(self, manager: PortfolioManager) -> None:
        snapshot = make_position_snapshot(
            fingerprint="pf-empty",
        )
        snapshot = replace(
            snapshot,
            positions=(),
            open_position_count=0,
            aggregate_unrealized_pnl=0.0,
            aggregate_quantity_by_underlying=MappingProxyType({}),
        )
        result = manager.ingest_position_snapshot(snapshot, make_ingest_context())
        assert result.status is PortfolioUpdateStatus.APPLIED
        assert result.metrics.open_position_count == 0
        assert result.exposure.gross_notional == 0.0

    def test_idempotent_reingest(self, manager: PortfolioManager) -> None:
        snapshot = make_position_snapshot(fingerprint="pf-idem")
        context = make_ingest_context()
        first = manager.ingest_position_snapshot(snapshot, context)
        second = manager.ingest_position_snapshot(snapshot, context)
        assert second.status is PortfolioUpdateStatus.NOOP
        assert first.update_fingerprint == second.update_fingerprint

    def test_all_stages_recorded(self, manager: PortfolioManager) -> None:
        result = manager.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-stages"),
            make_ingest_context(),
        )
        assert result.pipeline_summary.total_stages == 11
        assert result.pipeline_summary.passed_stages == 11

    def test_ingest_via_position_update_result(self, manager: PortfolioManager) -> None:
        snapshot = make_position_snapshot(fingerprint="pf-wrap")
        context = make_ingest_context(
            price_hints=MappingProxyType({"NFO:NIFTY24AUG25000CE": 130.0}),
        )
        result = manager.ingest_position_update_result(
            make_position_update_result(snapshot),
            context,
        )
        assert result.status is PortfolioUpdateStatus.APPLIED


class TestWarnings:
    def test_missing_mark_price_warning(self, manager: PortfolioManager) -> None:
        result = manager.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-mark"),
            make_ingest_context(),
        )
        assert any(w.code == WARN_PRICE_MARK_MISSING for w in result.warnings)

    def test_missing_greek_hint_warning(self, manager: PortfolioManager) -> None:
        config = fast_config(require_greek_hints=True)
        mgr = PortfolioManager(config)
        result = mgr.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-greek"),
            make_ingest_context(),
        )
        assert any(w.code == WARN_GREEK_HINT_MISSING for w in result.warnings)
        assert result.status is PortfolioUpdateStatus.PARTIAL

    def test_stale_greek_hint_warning(self) -> None:
        config = fast_config()
        mgr = PortfolioManager(config)
        hint = PositionGreekHint(
            position_id="pos-1",
            as_of=fixed_as_of() - timedelta(seconds=500),
            delta=0.1,
        )
        result = mgr.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-stale-greek"),
            make_ingest_context(greek_hints=MappingProxyType({"pos-1": hint})),
        )
        assert any(w.code == WARN_GREEK_HINT_STALE for w in result.warnings)

    def test_pnl_mismatch_warning(self, manager: PortfolioManager) -> None:
        snapshot = make_position_snapshot()
        snapshot = replace(snapshot, aggregate_unrealized_pnl=9999.0)
        result = manager.ingest_position_snapshot(
            snapshot,
            make_ingest_context(),
        )
        assert any(w.code == WARN_PNL_MISMATCH for w in result.warnings)

    def test_margin_hint_missing_warning(self, manager: PortfolioManager) -> None:
        context = replace(make_ingest_context(), margin_available_hint=None)
        result = manager.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-margin"),
            context,
        )
        assert any(w.code == WARN_MARGIN_HINT_MISSING for w in result.warnings)

    def test_margin_hint_stale_warning(self) -> None:
        mgr = PortfolioManager(fast_config())
        context = replace(
            make_ingest_context(),
            margin_hint_as_of=fixed_as_of() - timedelta(seconds=600),
        )
        result = mgr.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-stale-margin"),
            context,
        )
        assert any(w.code == WARN_MARGIN_HINT_STALE for w in result.warnings)

    def test_expiry_unresolved_warning(self) -> None:
        position = replace(
            make_position(),
            metadata=MappingProxyType(
                {
                    "underlying": "NIFTY",
                    "opened_at": "2026-08-04T04:30:00.000Z",
                    "correlation_id": "corr-1",
                }
            ),
        )
        result = PortfolioManager(fast_config()).ingest_position_snapshot(
            make_position_snapshot(position, fingerprint="pf-expiry"),
            make_ingest_context(),
        )
        assert any(w.code == WARN_EXPIRY_UNRESOLVED for w in result.warnings)

    def test_max_open_positions_warning(self) -> None:
        mgr = PortfolioManager(fast_config(max_open_positions=0))
        result = mgr.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-max-pos"),
            make_ingest_context(),
        )
        assert result.status is PortfolioUpdateStatus.PARTIAL
        assert any(w.code == ERROR_SNAPSHOT_INVALID for w in result.warnings)


class TestExposureAndAggregation:
    def test_exposure_by_underlying_and_strategy(self, manager: PortfolioManager) -> None:
        ce = make_position(position_id="ce", underlying="NIFTY")
        pe = make_position(
            position_id="pe",
            instrument_key="NFO:BANKNIFTY24AUG52000PE",
            underlying="BANKNIFTY",
            strategy_id="strat-b",
        )
        snapshot = make_position_snapshot(ce, pe, fingerprint="pf-exp")
        result = manager.ingest_position_snapshot(snapshot, make_ingest_context())
        assert "NIFTY" in result.exposure.gross_notional_by_underlying
        assert "BANKNIFTY" in result.exposure.gross_notional_by_underlying
        assert result.exposure.largest_underlying_weight_pct > 0
        assert "2026-08-28" in result.snapshot.by_expiry

    def test_peak_equity_tracking(self) -> None:
        mgr = PortfolioManager(fast_config(track_peak_equity=True))
        ctx = make_ingest_context(equity_hint=1_100_000.0)
        result = mgr.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-peak"),
            ctx,
        )
        assert result.metrics.peak_equity_hint == 1_100_000.0


class TestEvents:
    def test_lifecycle_events_published(self) -> None:
        bus = EventBus()
        captured: list[object] = []
        bus.subscribe("portfolio.*", lambda event: captured.append(event.payload))
        mgr = PortfolioManager(fast_config(), event_bus=bus)
        mgr.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-ev"),
            make_ingest_context(),
        )
        topics = {getattr(payload, "topic", "") for payload in captured}
        assert "portfolio.snapshot.published" in topics
        assert "portfolio.ingest.completed" in topics

    def test_on_position_snapshot_event_noop(self, manager: PortfolioManager) -> None:
        event = PositionEvent(
            event_type=PositionEventType.SNAPSHOT_PUBLISHED,
            topic="position.snapshot.published",
            update_id="u1",
            correlation_id="corr-1",
            occurred_at=fixed_as_of(),
        )
        manager.on_position_snapshot_event(event)
        assert manager.get_snapshot() is None

    def test_on_non_snapshot_event_ignored(self, manager: PortfolioManager) -> None:
        event = PositionEvent(
            event_type=PositionEventType.POSITION_OPENED,
            topic="position.opened",
            update_id="u1",
            correlation_id="corr-1",
            occurred_at=fixed_as_of(),
        )
        manager.on_position_snapshot_event(event)
        assert manager.get_snapshot() is None


class TestSerialization:
    def test_round_trip_update_result(self, manager: PortfolioManager) -> None:
        result = manager.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-ser"),
            make_ingest_context(),
        )
        payload = serialize_portfolio_update_result(result)
        restored = deserialize_portfolio_update_result(payload)
        assert restored.update_id == result.update_id
        assert restored.status == result.status
        assert restored.metrics.open_position_count == result.metrics.open_position_count

    def test_round_trip_snapshot(self, manager: PortfolioManager) -> None:
        result = manager.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-ser2"),
            make_ingest_context(),
        )
        payload = serialize_portfolio_snapshot(result.snapshot)
        restored = deserialize_portfolio_snapshot(payload)
        assert restored.snapshot_id == result.snapshot.snapshot_id
        assert len(restored.positions) == len(result.snapshot.positions)

    def test_malformed_json(self) -> None:
        with pytest.raises(PortfolioManagerValidationError) as exc:
            deserialize_portfolio_update_result("{bad")
        assert exc.value.code == ERROR_SERIALIZATION_MALFORMED

    def test_unsupported_schema_version(self, manager: PortfolioManager) -> None:
        import json

        result = manager.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-schema"),
            make_ingest_context(),
        )
        data = json.loads(serialize_portfolio_update_result(result))
        data["schema_version"] = "9.9.9"
        with pytest.raises(PortfolioManagerValidationError) as exc:
            deserialize_portfolio_update_result(json.dumps(data))
        assert exc.value.code == ERROR_SERIALIZATION_UNSUPPORTED_VERSION

    def test_snapshot_unsupported_schema_version(self, manager: PortfolioManager) -> None:
        import json

        result = manager.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-schema-snap"),
            make_ingest_context(),
        )
        data = json.loads(serialize_portfolio_snapshot(result.snapshot))
        data["schema_version"] = "9.9.9"
        with pytest.raises(PortfolioManagerValidationError) as exc:
            deserialize_portfolio_snapshot(json.dumps(data))
        assert exc.value.code == ERROR_SERIALIZATION_UNSUPPORTED_VERSION

    def test_deserialize_non_object_payload(self) -> None:
        with pytest.raises(PortfolioManagerValidationError) as exc:
            deserialize_portfolio_snapshot("[]")
        assert exc.value.code == ERROR_SERIALIZATION_MALFORMED


class TestDeterminism:
    def test_stable_fingerprint(self, manager: PortfolioManager) -> None:
        snapshot = make_position_snapshot(fingerprint="pf-det")
        context = make_ingest_context()
        first = manager.ingest_position_snapshot(snapshot, context)
        mgr2 = PortfolioManager(fast_config())
        second = mgr2.ingest_position_snapshot(snapshot, context)
        assert first.update_fingerprint == second.update_fingerprint

    def test_non_deterministic_snapshot_id(self) -> None:
        mgr = PortfolioManager(fast_config(deterministic_fingerprint=False))
        snapshot = make_position_snapshot(fingerprint="pf-nondet")
        result = mgr.ingest_position_snapshot(snapshot, make_ingest_context())
        assert result.snapshot.snapshot_id.startswith("pf-")

    def test_compute_update_fingerprint_helper(self, manager: PortfolioManager) -> None:
        snapshot = make_position_snapshot(fingerprint="pf-det2")
        result = manager.ingest_position_snapshot(snapshot, make_ingest_context())
        recomputed = compute_update_fingerprint(snapshot, result.snapshot, fast_config())
        assert recomputed == result.update_fingerprint


class TestValidation:
    def test_validate_ingest_context(self) -> None:
        snapshot = make_position_snapshot()
        result = validate_ingest_context(make_ingest_context(), snapshot, fast_config())
        assert result.is_valid

    def test_validate_invalid_execution_mode(self) -> None:
        snapshot = make_position_snapshot()
        context = replace(make_ingest_context(), execution_mode="invalid")  # type: ignore[arg-type]
        result = validate_ingest_context(context, snapshot, fast_config())
        assert not result.is_valid
        assert result.errors[0].code == ERROR_CONTEXT_INVALID

    def test_validate_open_count_mismatch(self, manager: PortfolioManager) -> None:
        result = manager.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-val-mismatch"),
            make_ingest_context(),
        )
        bad_exposure = replace(result.exposure, open_position_count=99)
        bad_result = replace(result, exposure=bad_exposure)
        validation = validate_portfolio_update_result(bad_result)
        assert not validation.is_valid
        assert validation.errors[0].code == ERROR_RESULT_INVALID

    def test_validate_update_result_valid(self, manager: PortfolioManager) -> None:
        result = manager.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-val"),
            make_ingest_context(),
        )
        validation = validate_portfolio_update_result(result)
        assert validation.is_valid
        assert_valid_portfolio_update_result(result)

    def test_assert_invalid_update_id(self) -> None:
        from portfolio.portfolio_manager import (
            PortfolioExposure,
            PortfolioMetrics,
            PortfolioPipelineResult,
            PortfolioSnapshot,
            PortfolioUpdateResult,
        )

        metrics = PortfolioMetrics(
            total_realized_pnl_session=0.0,
            total_unrealized_pnl=0.0,
            total_daily_pnl=0.0,
            equity_hint=1.0,
            cash_available_hint=1.0,
            capital_deployed=0.0,
            capital_utilization_pct=0.0,
            margin_used_hint=0.0,
            margin_available_hint=None,
            margin_utilization_pct=None,
            portfolio_delta=None,
            portfolio_gamma=None,
            portfolio_theta=None,
            portfolio_vega=None,
            open_position_count=0,
            peak_equity_hint=None,
            metrics_fingerprint="",
        )
        exposure = PortfolioExposure(
            gross_notional=0.0,
            net_notional=0.0,
            gross_notional_by_underlying=MappingProxyType({}),
            net_notional_by_underlying=MappingProxyType({}),
            exposure_by_strategy_id=MappingProxyType({}),
            exposure_by_strategy_family=MappingProxyType({}),
            exposure_by_expiry=MappingProxyType({}),
            largest_underlying_weight_pct=0.0,
            largest_strategy_weight_pct=0.0,
            open_position_count=0,
            open_position_count_by_underlying=MappingProxyType({}),
            exposure_fingerprint="",
        )
        snapshot = PortfolioSnapshot(
            snapshot_id="s",
            correlation_id="c",
            as_of=fixed_as_of(),
            account_id="a",
            metrics=metrics,
            exposure=exposure,
            positions=(),
            by_strategy=MappingProxyType({}),
            by_underlying=MappingProxyType({}),
            by_expiry=MappingProxyType({}),
            snapshot_fingerprint="",
        )
        result = PortfolioUpdateResult(
            update_id="",
            source_position_snapshot_id=None,
            correlation_id="c",
            status=PortfolioUpdateStatus.APPLIED,
            snapshot=snapshot,
            metrics=metrics,
            exposure=exposure,
            pipeline_summary=PortfolioPipelineResult(
                total_stages=0,
                passed_stages=0,
                failed_stage_id=None,
                stages=(),
                short_circuited=False,
            ),
            warnings=(),
            errors=(),
            primary_error_code=None,
            submitted_at=fixed_as_of(),
            completed_at=fixed_as_of(),
            duration_ms=0.0,
            update_fingerprint="",
        )
        with pytest.raises(PortfolioManagerValidationError) as exc:
            assert_valid_portfolio_update_result(result)
        assert exc.value.code == ERROR_RESULT_INVALID


class TestSnapshotIntegrity:
    def test_invalid_open_count_rejected(self, manager: PortfolioManager) -> None:
        snapshot = make_position_snapshot(fingerprint="pf-bad")
        snapshot = replace(snapshot, open_position_count=99)
        result = manager.ingest_position_snapshot(snapshot, make_ingest_context())
        assert result.status is PortfolioUpdateStatus.REJECTED
        assert result.primary_error_code == ERROR_SNAPSHOT_INVALID

    def test_empty_snapshot_id_rejected(self, manager: PortfolioManager) -> None:
        snapshot = replace(make_position_snapshot(fingerprint="pf-no-id"), snapshot_id="")
        result = manager.ingest_position_snapshot(snapshot, make_ingest_context())
        assert result.status is PortfolioUpdateStatus.REJECTED
        assert result.primary_error_code == ERROR_SNAPSHOT_INVALID

    def test_missing_fingerprint_warning(self, manager: PortfolioManager) -> None:
        snapshot = replace(make_position_snapshot(fingerprint="pf-no-fp"), snapshot_fingerprint="")
        result = manager.ingest_position_snapshot(snapshot, make_ingest_context())
        assert any(
            w.code == ERROR_SNAPSHOT_INVALID and "fingerprint missing" in w.message
            for w in result.warnings
        )


class TestQueryApi:
    def test_get_snapshot_metrics_exposure(self, manager: PortfolioManager) -> None:
        manager.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-query"),
            make_ingest_context(),
        )
        assert manager.get_snapshot() is not None
        assert manager.get_metrics() is not None
        assert manager.get_exposure() is not None
        assert manager.config.strict_correlation is True

    def test_get_metrics_exposure_before_ingest(self) -> None:
        mgr = PortfolioManager(fast_config())
        assert mgr.get_snapshot() is None
        assert mgr.get_metrics() is None
        assert mgr.get_exposure() is None


class TestThreadSafety:
    def test_concurrent_ingest(self) -> None:
        mgr = PortfolioManager(fast_config())

        def ingest_one(index: int) -> None:
            position = make_position(
                position_id=f"pos-{index}",
                instrument_key=f"NFO:INST{index}",
            )
            snapshot = make_position_snapshot(position, fingerprint=f"pf-{index}")
            mgr.ingest_position_snapshot(snapshot, make_ingest_context())

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(ingest_one, index) for index in range(4)]
            for future in futures:
                future.result()

        assert mgr.get_snapshot() is not None


class TestManagerValidationHelpers:
    def test_validate_helpers(self, manager: PortfolioManager) -> None:
        snapshot = make_position_snapshot(fingerprint="pf-help")
        context = make_ingest_context()
        assert manager.validate_ingest_context(context, snapshot).is_valid
        result = manager.ingest_position_snapshot(snapshot, context)
        assert manager.validate_update_result(result).is_valid


class TestAggregationBucket:
    def test_bucket_weight_pct(self, manager: PortfolioManager) -> None:
        result = manager.ingest_position_snapshot(
            make_position_snapshot(fingerprint="pf-bucket"),
            make_ingest_context(),
        )
        bucket = result.snapshot.by_strategy["short-strangle-v1"]
        assert isinstance(bucket, PortfolioAggregationBucket)
        assert bucket.weight_pct == 100.0
