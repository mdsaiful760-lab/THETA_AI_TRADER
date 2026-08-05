"""Unit tests for dashboard integration facade."""

from __future__ import annotations

import ast
import threading
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dashboard.dashboard_facade import (
    DASHBOARD_FACADE_SCHEMA_VERSION,
    DashboardFacade,
    DashboardFacadeConfigurationError,
    DashboardFacadeValidationError,
    DashboardIntegrationFacade,
    DashboardIntegrationFacadeConfig,
    FacadeMarketSnapshot,
    FacadeSystemStatus,
    empty_apme,
    empty_logs,
    empty_market_snapshot,
    empty_order_book,
    empty_paper_positions,
    empty_performance,
    empty_portfolio,
    empty_risk,
    empty_strategy_status,
    empty_system_status,
    to_jsonable,
)
from dashboard.facade import DashboardBackendFacade
from dashboard.utils.guards import collect_forbidden_imports


FIXED_NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)


class MutableClock:
    """Injectable clock that can be advanced for TTL tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or FIXED_NOW

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


def _offline_facade(**kwargs: object) -> DashboardIntegrationFacade:
    """Build offline facade with fixed clock."""
    return DashboardIntegrationFacade(
        session=None,
        clock=lambda: FIXED_NOW,
        **kwargs,
    )


class TestOfflineMode:
    """T01/T02: Offline getters return schema 1.0.0 and placeholders."""

    GETTERS = (
        "get_system_status",
        "get_market_snapshot",
        "get_strategy_status",
        "get_paper_positions",
        "get_order_book",
        "get_portfolio",
        "get_risk",
        "get_apme",
        "get_logs",
        "get_performance",
    )

    @pytest.mark.parametrize("method_name", GETTERS)
    def test_offline_getter_schema_and_source(self, method_name: str) -> None:
        facade = _offline_facade()
        method = getattr(facade, method_name)
        if method_name == "get_logs":
            result = method(limit=50)
        else:
            result = method()
        assert result.schema_version == DASHBOARD_FACADE_SCHEMA_VERSION
        assert result.source == "offline"
        assert result.as_of == FIXED_NOW

    def test_offline_system_status_disconnected(self) -> None:
        status = _offline_facade().get_system_status()
        assert status.system_status == "DISCONNECTED"
        assert status.broker_status == "N/A"
        assert status.facade_healthy is True

    def test_offline_is_connected_false(self) -> None:
        assert _offline_facade().is_connected is False

    def test_offline_health_offline(self) -> None:
        health = _offline_facade().get_facade_health()
        assert health.status == "OFFLINE"
        assert health.connected is False
        assert health.schema_version == DASHBOARD_FACADE_SCHEMA_VERSION

    def test_offline_placeholders_no_invented_pnl(self) -> None:
        facade = _offline_facade()
        paper = facade.get_paper_positions()
        assert paper.virtual_cash == "—"
        assert paper.realized_pnl == "—"
        assert paper.unrealized_pnl == "—"
        portfolio = facade.get_portfolio()
        assert portfolio.equity == "—"
        market = facade.get_market_snapshot()
        assert market.ltp == "—"

    def test_smoke_all_ten_domains(self) -> None:
        facade = _offline_facade()
        for name in self.GETTERS:
            getattr(facade, name)(**( {"limit": 10} if name == "get_logs" else {}))


class TestConfigValidation:
    """T03: Config invariant failures raise CFG-DIF-* codes."""

    def test_invalid_schema_version(self) -> None:
        with pytest.raises(DashboardFacadeConfigurationError) as exc:
            DashboardIntegrationFacadeConfig(schema_version="0.0.1")
        assert exc.value.code == "CFG-DIF-001"

    def test_invalid_cache_ttl(self) -> None:
        with pytest.raises(DashboardFacadeConfigurationError) as exc:
            DashboardIntegrationFacadeConfig(cache_ttl_seconds=-1)
        assert exc.value.code == "CFG-DIF-002"

    def test_invalid_log_limit(self) -> None:
        with pytest.raises(DashboardFacadeConfigurationError) as exc:
            DashboardIntegrationFacadeConfig(log_limit_default=0)
        assert exc.value.code == "CFG-DIF-003"

    def test_empty_placeholder(self) -> None:
        with pytest.raises(DashboardFacadeConfigurationError) as exc:
            DashboardIntegrationFacadeConfig(placeholder="")
        assert exc.value.code == "CFG-DIF-004"


class TestCachingAndRefresh:
    """T05/T06: TTL cache and refresh behaviour."""

    def test_refresh_clears_cache(self) -> None:
        clock = MutableClock()
        config = DashboardIntegrationFacadeConfig(cache_ttl_seconds=60.0)
        facade = DashboardIntegrationFacade(
            session=None,
            config=config,
            clock=clock,
        )
        facade.get_market_snapshot()
        facade.get_system_status()
        assert facade.get_facade_health().cache_entries == 2
        result = facade.refresh()
        assert result.cache_cleared is True
        assert result.success is True
        health = facade.get_facade_health()
        assert health.cache_entries == 1

    def test_ttl_cache_returns_cached_source(self) -> None:
        clock = MutableClock()
        session = MagicMock()
        session.get_health.return_value = SimpleNamespace(
            session_state=SimpleNamespace(value="running"),
            overall_status=SimpleNamespace(value="healthy"),
            broker_connection=SimpleNamespace(state="connected", connected=True),
            message="ok",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="PAPER",
            market_status="OPEN",
        )
        config = DashboardIntegrationFacadeConfig(cache_ttl_seconds=30.0)
        facade = DashboardIntegrationFacade(session=session, config=config, clock=clock)
        live = facade.get_system_status()
        assert live.source == "live"
        cached = facade.get_system_status()
        assert cached.source == "cached"
        clock.advance(31.0)
        refreshed = facade.get_system_status()
        assert refreshed.source == "live"
        assert session.get_health.call_count >= 2

    def test_get_logs_respects_limit(self) -> None:
        session = MagicMock()
        session.get_health.return_value = SimpleNamespace(
            session_state="running",
            overall_status="healthy",
            broker_connection=None,
            message="ok",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="PAPER",
            market_status="OPEN",
        )
        entries = [
            SimpleNamespace(
                timestamp="t",
                level="INFO",
                message=f"line-{i}",
                logger="test",
            )
            for i in range(10)
        ]
        session.get_logs.return_value = SimpleNamespace(entries=entries)
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        logs = facade.get_logs(limit=3)
        assert logs.limit == 3
        assert len(logs.entries) == 3


class TestUpstreamDegrade:
    """T06: Upstream exceptions produce degraded placeholders."""

    def test_health_exception_degrades(self) -> None:
        session = MagicMock()
        session.get_health.side_effect = RuntimeError("boom")
        session.get_runtime_state.return_value = SimpleNamespace()
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        status = facade.get_system_status()
        assert status.system_status == "DISCONNECTED"
        assert status.facade_healthy is False
        health = facade.get_facade_health()
        assert health.last_error_code == "DIF.UPSTREAM.ERROR"

    def test_missing_optional_accessor_unsupported(self) -> None:
        session = MagicMock(spec=["get_health", "get_runtime_state"])
        session.get_health.return_value = SimpleNamespace(
            session_state=SimpleNamespace(value="running"),
            overall_status=SimpleNamespace(value="healthy"),
            broker_connection=SimpleNamespace(state="connected", connected=True),
            message="ok",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="PAPER",
            market_status="OPEN",
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        market = facade.get_market_snapshot()
        assert market.source == "offline" or market.ltp == "—"
        health = facade.get_facade_health()
        assert health.last_error_code == "DIF.UPSTREAM.UNSUPPORTED"

    def test_live_mapping_populates_rows(self) -> None:
        session = MagicMock()
        session.get_health.return_value = SimpleNamespace(
            session_state=SimpleNamespace(value="running"),
            overall_status=SimpleNamespace(value="healthy"),
            broker_connection=SimpleNamespace(state="connected", connected=True),
            message="live",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="PAPER",
            market_status="OPEN",
        )
        session.get_market_snapshot.return_value = SimpleNamespace(
            underlyings=["NIFTY"],
            selected_underlying="NIFTY",
            ltp=22000.5,
            change="+0.5%",
            volume="1M",
            option_chain_columns=["strike", "ltp"],
            option_chain_rows=[("22000", "100")],
        )
        session.get_strategy_status.return_value = SimpleNamespace(
            strategies=[
                SimpleNamespace(
                    strategy_id="ss-1",
                    family="short_strangle",
                    status="ACTIVE",
                    confidence="0.82",
                    last_signal="HOLD",
                    timestamp="2026-08-05T12:00:00Z",
                    reasons=("liquidity_ok",),
                )
            ]
        )
        session.get_paper_positions.return_value = SimpleNamespace(
            virtual_cash="100000",
            realized_pnl="500",
            unrealized_pnl="200",
            positions=[
                SimpleNamespace(
                    symbol="NIFTY",
                    quantity="1",
                    avg_price="100",
                    mark="105",
                    pnl="5",
                )
            ],
            equity_series=[("2026-08-05", 100000.0)],
        )
        session.get_order_book.return_value = SimpleNamespace(
            orders=[
                SimpleNamespace(
                    order_id="o1",
                    plan_id="p1",
                    status="FILLED",
                    symbol="NIFTY",
                    side="BUY",
                    quantity="1",
                    timestamp="t",
                )
            ]
        )
        session.get_portfolio.return_value = SimpleNamespace(
            equity="100000",
            exposure="50000",
            utilization="50%",
            positions=[
                SimpleNamespace(symbol="NIFTY", quantity="1", exposure="50000", pnl="5")
            ],
            equity_series=[("2026-08-05", 100000.0)],
            allocation=[("NIFTY", 1.0)],
        )
        session.get_risk.return_value = SimpleNamespace(
            verdict="APPROVED",
            reason_codes=("RISK_OK",),
            limits={"max_loss": "1000", "api_key": "secret-value"},
        )
        session.get_apme.return_value = SimpleNamespace(
            summary="No actions",
            decisions=[
                SimpleNamespace(
                    position_id="pos-1",
                    action="HOLD",
                    rationale="within bounds",
                    timestamp="t",
                )
            ],
        )
        session.get_logs.return_value = SimpleNamespace(
            entries=[
                SimpleNamespace(
                    timestamp="t",
                    level="INFO",
                    message="token=abc123",
                    logger="app",
                )
            ]
        )
        session.get_performance.return_value = SimpleNamespace(
            metrics={"win_rate": "55%", "expectancy": "0.2"},
            series=[("2026-08-05", 1.0)],
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        assert facade.is_connected is True
        assert facade.get_market_snapshot().ltp == "22000.5"
        strategy = facade.get_strategy_status()
        assert len(strategy.strategies) == 4
        assert strategy.strategies[0].family == "short_strangle"
        assert strategy.strategies[0].status == "ACTIVE"
        assert strategy.strategies[0].eligibility == "Eligible"
        assert facade.get_paper_positions().virtual_cash == "100000"
        assert len(facade.get_order_book().orders) == 1
        assert facade.get_portfolio().equity == "100000"
        risk = facade.get_risk()
        assert risk.verdict == "APPROVED"
        assert any(v == "***" for _, v in risk.limits)
        assert len(facade.get_apme().decisions) == 1
        assert facade.get_performance().metrics[0][0] == "win_rate"


class TestLogRedaction:
    """T07: Log redaction masks secret-like content."""

    def test_redacts_token_in_message(self) -> None:
        session = MagicMock()
        session.get_health.return_value = SimpleNamespace(
            session_state="running",
            overall_status="healthy",
            broker_connection=None,
            message="ok",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="PAPER",
            market_status="OPEN",
        )
        session.get_logs.return_value = SimpleNamespace(
            entries=[
                SimpleNamespace(
                    timestamp="t",
                    level="WARN",
                    message="api_key=supersecretvalue",
                    logger="auth",
                )
            ]
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        entry = facade.get_logs(limit=10).entries[0]
        assert "supersecretvalue" not in entry.message
        assert "***" in entry.message


class TestPresentationAdapter:
    """T08: Presentation adapter maps without altering placeholders."""

    def test_adapter_implements_protocol(self) -> None:
        facade = _offline_facade()
        adapter = facade.as_presentation_facade()
        assert isinstance(adapter, DashboardBackendFacade)

    def test_adapter_offline_health_disconnected(self) -> None:
        adapter = _offline_facade().as_presentation_facade()
        health = adapter.get_health()
        assert health.status == "DISCONNECTED"

    def test_adapter_maps_market_and_analytics(self) -> None:
        session = MagicMock()
        session.get_health.return_value = SimpleNamespace(
            session_state=SimpleNamespace(value="running"),
            overall_status=SimpleNamespace(value="healthy"),
            broker_connection=SimpleNamespace(state="connected", connected=True),
            message="ok",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="PAPER",
            market_status="OPEN",
        )
        session.get_market_snapshot.return_value = SimpleNamespace(
            underlyings=("NIFTY",),
            selected_underlying="NIFTY",
            ltp="—",
            change="—",
            volume="—",
            option_chain_columns=("strike",),
            option_chain_rows=(),
        )
        session.get_performance.return_value = SimpleNamespace(metrics={}, series=())
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        adapter = facade.as_presentation_facade()
        market = adapter.get_market_snapshot()
        assert market.ltp == "—"
        analytics = adapter.get_analytics()
        assert analytics.available is False

    def test_adapter_refresh_snapshots(self) -> None:
        adapter = _offline_facade().as_presentation_facade()
        result = adapter.refresh_snapshots()
        assert result.success is True


class TestThreadSafety:
    """T04: Concurrent getter calls do not corrupt cache."""

    def test_concurrent_gets(self) -> None:
        config = DashboardIntegrationFacadeConfig(cache_ttl_seconds=5.0)
        facade = DashboardIntegrationFacade(
            session=None,
            config=config,
            clock=lambda: FIXED_NOW,
        )
        errors: list[Exception] = []
        results: list[FacadeMarketSnapshot] = []

        def worker() -> None:
            try:
                for _ in range(20):
                    results.append(facade.get_market_snapshot())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        assert all(r.schema_version == DASHBOARD_FACADE_SCHEMA_VERSION for r in results)


class TestFrozenDtos:
    """T11: DTOs are immutable."""

    def test_system_status_frozen(self) -> None:
        dto = empty_system_status(as_of=FIXED_NOW)
        with pytest.raises(FrozenInstanceError):
            dto.system_status = "RUNNING"  # type: ignore[misc]

    def test_to_jsonable_round_trip_fields(self) -> None:
        dto = empty_market_snapshot(as_of=FIXED_NOW)
        payload = to_jsonable(dto)
        assert payload["schema_version"] == DASHBOARD_FACADE_SCHEMA_VERSION
        assert isinstance(payload["as_of"], str)

    def test_to_jsonable_rejects_unknown(self) -> None:
        with pytest.raises(DashboardFacadeValidationError):
            to_jsonable(42)  # type: ignore[arg-type]


class TestForbiddenImports:
    """T09: No broker/strategy/risk engine imports."""

    def test_no_forbidden_imports(self) -> None:
        import dashboard.dashboard_facade as module

        tree = ast.parse(open(module.__file__, encoding="utf-8").read())
        forbidden = collect_forbidden_imports(tree)
        assert forbidden == ()


class TestLifecyclePassthrough:
    """Optional start/stop delegation."""

    def test_lifecycle_disabled_by_default(self) -> None:
        session = MagicMock()
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        start = facade.start()
        assert start.success is False
        assert start.code == "DIF.LIFECYCLE.DISABLED"
        stop = facade.stop()
        assert stop.code == "DIF.LIFECYCLE.DISABLED"

    def test_lifecycle_start_stop_when_enabled(self) -> None:
        session = MagicMock()
        config = DashboardIntegrationFacadeConfig(enable_lifecycle_passthrough=True)
        facade = DashboardIntegrationFacade(
            session=session,
            config=config,
            clock=lambda: FIXED_NOW,
        )
        assert facade.start().success is True
        assert facade.stop().success is True
        session.start.assert_called_once()
        session.stop.assert_called_once()

    def test_lifecycle_no_session(self) -> None:
        config = DashboardIntegrationFacadeConfig(enable_lifecycle_passthrough=True)
        facade = DashboardIntegrationFacade(session=None, config=config, clock=lambda: FIXED_NOW)
        result = facade.start()
        assert result.code == "DIF.SESSION.UNAVAILABLE"

    def test_lifecycle_unsupported_start_stop(self) -> None:
        session = SimpleNamespace()
        config = DashboardIntegrationFacadeConfig(enable_lifecycle_passthrough=True)
        facade = DashboardIntegrationFacade(session=session, config=config, clock=lambda: FIXED_NOW)
        assert facade.start().code == "DIF.UPSTREAM.UNSUPPORTED"
        assert facade.stop().code == "DIF.UPSTREAM.UNSUPPORTED"

    def test_lifecycle_start_stop_errors(self) -> None:
        session = MagicMock()
        session.start.side_effect = RuntimeError("start failed")
        session.stop.side_effect = RuntimeError("stop failed")
        config = DashboardIntegrationFacadeConfig(enable_lifecycle_passthrough=True)
        facade = DashboardIntegrationFacade(session=session, config=config, clock=lambda: FIXED_NOW)
        assert facade.start().code == "DIF.UPSTREAM.ERROR"
        assert facade.stop().code == "DIF.UPSTREAM.ERROR"


class TestSystemStatusMapping:
    """Cover system status state and broker mapping branches."""

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            ("degraded", "DEGRADED"),
            ("stopped", "STOPPED"),
            ("stopping", "STOPPED"),
            ("failed", "DISCONNECTED"),
        ],
    )
    def test_session_state_mapping(self, state: str, expected: str) -> None:
        session = MagicMock()
        session.get_health.return_value = SimpleNamespace(
            session_state=SimpleNamespace(value=state),
            overall_status=None,
            broker_connection=SimpleNamespace(state="disconnected", connected=False),
            message="msg",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="LIVE",
            market_status="CLOSED",
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        assert facade.get_system_status().system_status == expected

    def test_broker_connected_via_flag(self) -> None:
        session = MagicMock()
        session.get_health.return_value = SimpleNamespace(
            session_state=SimpleNamespace(value="running"),
            overall_status=None,
            broker_connection=SimpleNamespace(state="unknown", connected=True),
            message="msg",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="PAPER",
            market_status="OPEN",
        )
        assert (
            DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
            .get_system_status()
            .broker_status
            == "CONNECTED"
        )

    def test_is_connected_false_on_unknown_status(self) -> None:
        session = MagicMock()
        session.get_health.return_value = SimpleNamespace(
            session_state=SimpleNamespace(value="failed"),
            overall_status=None,
            broker_connection=None,
            message="msg",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="ANALYSIS",
            market_status="UNKNOWN",
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        assert facade.is_connected is False

    def test_is_connected_exception_returns_false(self) -> None:
        session = MagicMock()
        session.get_health.side_effect = ValueError("bad")
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        assert facade.is_connected is False

    def test_facade_health_degraded_with_error(self) -> None:
        session = MagicMock(spec=["get_health", "get_runtime_state"])
        session.get_health.return_value = SimpleNamespace(
            session_state=SimpleNamespace(value="running"),
            overall_status=SimpleNamespace(value="healthy"),
            broker_connection=SimpleNamespace(state="connected", connected=True),
            message="ok",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="PAPER",
            market_status="OPEN",
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        facade.get_market_snapshot()
        health = facade.get_facade_health()
        assert health.status == "DEGRADED"
        assert health.last_error_code == "DIF.UPSTREAM.UNSUPPORTED"


class TestHelperAndEdgePaths:
    """Cover serialization helpers and alternate upstream shapes."""

    def test_to_jsonable_accepts_mapping(self) -> None:
        payload = to_jsonable({"as_of": FIXED_NOW, "values": (1, 2)})
        assert payload["values"] == [1, 2]

    def test_utc_now_used_when_as_of_missing(self) -> None:
        dto = empty_system_status()
        assert dto.as_of.tzinfo is not None

    def test_custom_offline_placeholder_message(self) -> None:
        config = DashboardIntegrationFacadeConfig(placeholder="N/A")
        facade = DashboardIntegrationFacade(session=None, config=config, clock=lambda: FIXED_NOW)
        assert facade.get_system_status().message == "N/A"

    def test_alternate_upstream_accessors(self) -> None:
        session = MagicMock(spec=["get_health", "get_runtime_state"])
        session.get_health.return_value = SimpleNamespace(
            session_state=SimpleNamespace(value="running"),
            overall_status=SimpleNamespace(value="healthy"),
            broker_connection=SimpleNamespace(state="connected", connected=True),
            message="ok",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="PAPER",
            market_status="OPEN",
        )
        session.get_strategy_evaluation_summary = MagicMock(
            return_value=SimpleNamespace(
                rows=[
                    SimpleNamespace(
                        strategy_id="x",
                        family="f",
                        status="s",
                        confidence="c",
                        signal="sig",
                        timestamp="t",
                        reasons=("r",),
                    )
                ]
            )
        )
        session.get_paper_trading_snapshot = MagicMock(
            return_value=SimpleNamespace(
                virtual_cash="1",
                realized_pnl="2",
                unrealized_pnl="3",
                positions=[],
                equity_series=[],
            )
        )
        session.get_orders_snapshot = MagicMock(return_value=SimpleNamespace(orders=[]))
        session.get_portfolio_snapshot = MagicMock(
            return_value=SimpleNamespace(
                equity="1",
                exposure="2",
                utilization="3",
                positions=[],
                equity_series=[],
                allocation_series=[],
            )
        )
        session.get_risk_decision = MagicMock(
            return_value=SimpleNamespace(
                verdict="SKIPPED",
                reason_codes=[],
                limits=[("max", "1")],
            )
        )
        session.get_apme_decisions = MagicMock(
            return_value=SimpleNamespace(summary="s", decisions=[])
        )
        session.get_log_buffer = MagicMock(
            return_value=[
                SimpleNamespace(
                    timestamp="t",
                    level="INFO",
                    message="ok",
                    source="engine",
                )
            ]
        )
        session.get_analytics = MagicMock(
            return_value=SimpleNamespace(
                metrics=[("win_rate", "50%")],
                performance_series=[{"label": "d1", "value": 1.0}],
            )
        )
        facade = DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)
        # Unknown family "f" does not map into the four monitor slots.
        status = facade.get_strategy_status()
        assert len(status.strategies) == 4
        assert all(row.status == "—" for row in status.strategies)
        assert facade.get_paper_positions().virtual_cash == "1"
        assert facade.get_order_book().orders == ()
        assert facade.get_portfolio().equity == "1"
        assert facade.get_risk().verdict == "SKIPPED"
        assert facade.get_apme().summary == "s"
        assert len(facade.get_logs(limit=5).entries) == 1
        perf = facade.get_performance()
        assert perf.metrics == (("win_rate", "50%"),)

    def test_long_log_message_truncated(self) -> None:
        session = MagicMock()
        session.get_health.return_value = SimpleNamespace(
            session_state="running",
            overall_status="healthy",
            broker_connection=None,
            message="ok",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="PAPER",
            market_status="OPEN",
        )
        long_message = "x" * 3000
        session.get_logs.return_value = SimpleNamespace(
            entries=[
                SimpleNamespace(
                    timestamp="t",
                    level="INFO",
                    message=long_message,
                    logger="app",
                )
            ]
        )
        entry = DashboardIntegrationFacade(
            session=session,
            clock=lambda: FIXED_NOW,
        ).get_logs(limit=1).entries[0]
        assert len(entry.message) <= 2001
        assert entry.message.endswith("…")

    def test_refresh_failure_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = _offline_facade()
        monkeypatch.setattr(
            facade,
            "get_system_status",
            MagicMock(side_effect=RuntimeError("refresh boom")),
        )
        result = facade.refresh()
        assert result.success is False
        assert "refresh boom" in result.message


class TestPresentationAdapterExtended:
    """Cover remaining presentation adapter mappings."""

    def _live_facade(self) -> DashboardIntegrationFacade:
        session = MagicMock()
        session.get_health.return_value = SimpleNamespace(
            session_state=SimpleNamespace(value="running"),
            overall_status=SimpleNamespace(value="healthy"),
            broker_connection=SimpleNamespace(state="connected", connected=True),
            message="ok",
        )
        session.get_runtime_state.return_value = SimpleNamespace(
            execution_mode="PAPER",
            market_status="OPEN",
        )
        session.get_strategy_status.return_value = SimpleNamespace(
            market_regime="RANGE_BOUND",
            active_strategy="short_strangle",
            confidence_score=0.9,
            evaluated_at="2026-08-05 12:00:00 UTC",
            strategies=[
                SimpleNamespace(
                    strategy_id="s1",
                    family="short_strangle",
                    display_name="Short Strangle",
                    status="ACTIVE",
                    confidence="0.9",
                    last_signal="BUY",
                    timestamp="t",
                    reasons=("ok",),
                    ranking_score=82.5,
                    eligible=True,
                )
            ]
        )
        session.get_paper_positions.return_value = SimpleNamespace(
            virtual_cash="100",
            realized_pnl="1",
            unrealized_pnl="2",
            positions=[
                SimpleNamespace(
                    symbol="NIFTY",
                    quantity="1",
                    avg_price="1",
                    mark="2",
                    pnl="1",
                )
            ],
            equity_series=(),
        )
        session.get_order_book.return_value = SimpleNamespace(
            orders=[
                SimpleNamespace(
                    order_id="1",
                    plan_id="p",
                    status="NEW",
                    symbol="NIFTY",
                    side="BUY",
                    quantity="1",
                    timestamp="t",
                )
            ]
        )
        session.get_portfolio.return_value = SimpleNamespace(
            equity="1",
            exposure="2",
            utilization="3",
            positions=[
                SimpleNamespace(symbol="NIFTY", quantity="1", exposure="2", pnl="1")
            ],
            equity_series=(),
            allocation=(),
        )
        session.get_risk.return_value = SimpleNamespace(
            verdict="REJECTED",
            reason_codes=("R1",),
            limits={"max": "1"},
        )
        session.get_apme.return_value = SimpleNamespace(
            summary="hold",
            decisions=[
                SimpleNamespace(
                    position_id="p1",
                    action="HOLD",
                    rationale="ok",
                    timestamp="t",
                )
            ],
        )
        session.get_logs.return_value = SimpleNamespace(
            entries=[
                SimpleNamespace(
                    timestamp="t",
                    level="INFO",
                    message="event",
                    logger="app",
                )
            ]
        )
        session.get_performance.return_value = SimpleNamespace(
            metrics={"win_rate": "60%", "expectancy": "0.3"},
            series=[("d", 1.0)],
        )
        return DashboardIntegrationFacade(session=session, clock=lambda: FIXED_NOW)

    def test_adapter_full_page_mappings(self) -> None:
        adapter = self._live_facade().as_presentation_facade()
        assert adapter.get_runtime_state().connected is True
        home = adapter.get_home_snapshot()
        assert home.kpis.active_strategy == "Short Strangle"
        assert home.kpis.market_regime == "RANGE_BOUND"
        monitor = adapter.get_strategy_monitor()
        assert len(monitor.strategies) == 4
        assert monitor.strategies[0].score == "82.50"
        assert monitor.strategies[0].eligibility == "Eligible"
        assert monitor.active_strategy == "Short Strangle"
        assert len(adapter.get_paper_trading().positions) == 1
        assert len(adapter.get_orders().orders) == 1
        assert len(adapter.get_portfolio().positions) == 1
        assert adapter.get_risk().verdict == "REJECTED"
        assert len(adapter.get_apme().decisions) == 1
        assert len(adapter.get_logs(limit=10).entries) == 1
        analytics = adapter.get_analytics()
        assert analytics.available is True
        assert analytics.win_rate == "60%"
        settings = adapter.get_settings_view()
        assert settings.config_entries["schema_version"] == DASHBOARD_FACADE_SCHEMA_VERSION

    def test_adapter_lifecycle_delegates(self) -> None:
        session = MagicMock()
        config = DashboardIntegrationFacadeConfig(enable_lifecycle_passthrough=True)
        facade = DashboardIntegrationFacade(
            session=session,
            config=config,
            clock=lambda: FIXED_NOW,
        )
        adapter = facade.as_presentation_facade()
        assert adapter.start().success is True
        assert adapter.stop().success is True


class TestEmptyFactoriesAndAlias:
    """Cover helpers and module alias."""

    def test_empty_factories(self) -> None:
        assert empty_order_book(as_of=FIXED_NOW).orders == ()
        assert len(empty_strategy_status(as_of=FIXED_NOW).strategies) == 4
        assert empty_strategy_status(as_of=FIXED_NOW).source == "offline"
        assert empty_paper_positions(as_of=FIXED_NOW).positions == ()
        assert empty_portfolio(as_of=FIXED_NOW).equity == "—"
        assert empty_risk(as_of=FIXED_NOW).verdict == "—"
        assert empty_apme(as_of=FIXED_NOW).decisions == ()
        assert empty_logs(as_of=FIXED_NOW, limit=5).limit == 5
        assert empty_performance(as_of=FIXED_NOW).metrics == ()

    def test_dashboard_facade_alias(self) -> None:
        assert DashboardFacade is DashboardIntegrationFacade

    def test_schema_version_property(self) -> None:
        assert _offline_facade().schema_version == DASHBOARD_FACADE_SCHEMA_VERSION
