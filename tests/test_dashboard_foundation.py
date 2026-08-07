"""Unit tests for dashboard foundation package."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from dashboard import DASHBOARD_UI_SCHEMA_VERSION, DASHBOARD_VERSION, default_dashboard_ui_config
from dashboard.app import build_render_context, resolve_page
from dashboard.components.chart_placeholder import render_tradingview_placeholder
from dashboard.components.sidebar import PAGE_IDS
from dashboard.config import DashboardConfigurationError, DashboardUiConfig
from dashboard.facade import NullIntegrationFacade
from dashboard.pages import PAGE_REGISTRY
from dashboard.utils.formatting import format_money, format_percent, format_timestamp
from dashboard.utils.guards import (
    ForbiddenDashboardImportError,
    assert_no_forbidden_dashboard_imports,
)
from dashboard.utils.polling import should_autorefresh
from dashboard.view_models import home_kpi_cards


def _mock_streamlit() -> MagicMock:
    """Build a lightweight Streamlit mock with common widget stubs."""
    mock_st = MagicMock()
    mock_st.columns.side_effect = (
        lambda n, **kwargs: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
    )
    mock_st.expander.return_value.__enter__ = MagicMock(return_value=None)
    mock_st.expander.return_value.__exit__ = MagicMock(return_value=None)
    mock_st.selectbox.side_effect = lambda *args, **kwargs: kwargs.get("options", ["All"])[0]
    mock_st.radio.side_effect = lambda *args, **kwargs: kwargs.get("options", ["Home"])[0]
    mock_st.number_input.return_value = 2.0
    mock_st.button.return_value = False
    mock_st.components = MagicMock()
    mock_st.components.v1 = MagicMock()
    mock_st.components.v1.html = MagicMock()
    return mock_st


def _render_context():
    """Build a render context with mocked Streamlit session state."""
    fixed = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
    session_payload = {
        "active_page": "home",
        "last_error": None,
        "last_refresh_at": None,
        "facade_action_pending": False,
        "ui_prefs": {},
    }
    mock_state = MagicMock()
    mock_state.__contains__.return_value = True
    mock_state.__getitem__.return_value = session_payload
    with patch("dashboard.session_state.st.session_state", mock_state):
        return build_render_context(
            facade=NullIntegrationFacade(),
            clock=lambda: fixed,
        )


def _patch_page_streamlit(mock_st: MagicMock):
    """Patch streamlit symbols used by page and component modules."""
    return patch.multiple(
        "streamlit",
        markdown=mock_st.markdown,
        info=mock_st.info,
        warning=mock_st.warning,
        error=mock_st.error,
        caption=mock_st.caption,
        subheader=mock_st.subheader,
        text=mock_st.text,
        write=mock_st.write,
        metric=mock_st.metric,
        columns=mock_st.columns,
        selectbox=mock_st.selectbox,
        number_input=mock_st.number_input,
        button=mock_st.button,
        success=mock_st.success,
        expander=mock_st.expander,
        dataframe=mock_st.dataframe,
        plotly_chart=mock_st.plotly_chart,
        bar_chart=mock_st.bar_chart,
        divider=mock_st.divider,
        radio=mock_st.radio,
    )


class TestDashboardUiConfig:
    """T01: Dashboard UI config defaults and validation."""

    def test_defaults(self) -> None:
        config = default_dashboard_ui_config()
        assert config.schema_version == DASHBOARD_UI_SCHEMA_VERSION
        assert config.app_title == "THETA AI TRADER"
        assert config.default_page == "home"
        assert config.refresh_interval_seconds == 2.0
        assert config.index_symbols == ("NIFTY", "BANKNIFTY", "SENSEX", "INDIA VIX")

    def test_invalid_schema_version(self) -> None:
        with pytest.raises(DashboardConfigurationError) as exc:
            DashboardUiConfig(schema_version="0.0.1")
        assert exc.value.code == "CFG-DASH-001"

    def test_invalid_default_page(self) -> None:
        with pytest.raises(DashboardConfigurationError) as exc:
            DashboardUiConfig(default_page="invalid")
        assert exc.value.code == "CFG-DASH-003"

    def test_invalid_refresh_interval(self) -> None:
        with pytest.raises(DashboardConfigurationError) as exc:
            DashboardUiConfig(refresh_interval_seconds=0)
        assert exc.value.code == "CFG-DASH-004"


class TestPageRegistry:
    """T02/T04: Page registry completeness and sidebar alignment."""

    def test_registry_has_twenty_two_pages(self) -> None:
        assert len(PAGE_REGISTRY) == 22

    def test_registry_page_ids(self) -> None:
        expected = {
            "home",
            "market_regime",
            "greeks",
            "liquidity",
            "volatility",
            "option_chain",
            "heatmap",
            "scanner",
            "builder",
            "backtesting",
            "library",
            "risk_dashboard",
            "position_sizing",
            "portfolio",
            "exposure",
            "trade_execution",
            "orders",
            "positions",
            "trade_log",
            "engine_status",
            "logs",
            "settings",
        }
        assert set(PAGE_REGISTRY) == expected

    def test_sidebar_page_ids_match_registry(self) -> None:
        assert PAGE_IDS == tuple(PAGE_REGISTRY.keys())


class TestPageRenderSmoke:
    """T03: Each page render runs with null facade."""

    @pytest.mark.parametrize("page_id", list(PAGE_REGISTRY.keys()))
    def test_page_render_with_mock_streamlit(self, page_id: str) -> None:
        ctx = _render_context()
        page = PAGE_REGISTRY[page_id]
        mock_st = _mock_streamlit()
        with _patch_page_streamlit(mock_st):
            with patch("dashboard.components.chart_placeholder.st", mock_st):
                with patch(
                    "streamlit.components.v1.html",
                    mock_st.components.v1.html,
                ):
                    page.render(ctx)


class TestHomePlaceholders:
    """T05/T06/T07: Home terminal placeholders and components."""

    def test_home_indices_include_four_symbols(self) -> None:
        facade = NullIntegrationFacade()
        snapshot = facade.get_home_snapshot()
        symbols = {quote.symbol for quote in snapshot.indices}
        assert symbols == {"NIFTY", "BANKNIFTY", "SENSEX", "INDIA VIX"}

    def test_kpi_cards_have_five_labels(self) -> None:
        facade = NullIntegrationFacade()
        cards = home_kpi_cards(facade.get_home_snapshot().kpis)
        assert len(cards) == 5
        labels = {card.label for card in cards}
        assert labels == {
            "Active Strategy",
            "Confidence",
            "Market Regime",
            "Paper PnL",
            "Open Positions",
        }

    def test_chart_placeholder_renders_without_tv_js(self) -> None:
        mock_st = _mock_streamlit()
        with patch("dashboard.components.chart_placeholder.st", mock_st):
            render_tradingview_placeholder(height=200)
        mock_st.markdown.assert_called()
        mock_st.components.v1.html.assert_called()


class TestFacadeActions:
    """T08/T12: Facade lifecycle without engines."""

    def test_null_facade_start_stop_refresh(self) -> None:
        facade = NullIntegrationFacade()
        start = facade.start()
        stop = facade.stop()
        refresh = facade.refresh_snapshots()
        assert start.success is False
        assert stop.success is False
        assert refresh.success is True
        assert facade.is_connected is False

    def test_autorefresh_flag_does_not_imply_trading_cycle(self) -> None:
        config = DashboardUiConfig(enable_autorefresh=True, refresh_interval_seconds=2.0)
        now = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
        assert should_autorefresh(config, last_refresh_at=None, now=now) is True
        assert config.enable_autorefresh is True


class TestForbiddenImportGuard:
    """T09: Forbidden import guard."""

    def test_guard_fails_on_broker_import(self) -> None:
        source = "import kiteconnect\n"
        tree = ast.parse(source)
        with pytest.raises(ForbiddenDashboardImportError):
            assert_no_forbidden_dashboard_imports(tree)


class TestFormatters:
    """T10: Deterministic formatters."""

    def test_format_money(self) -> None:
        assert format_money(None) == "—"
        assert format_money(1234.5) == "INR 1,234.50"

    def test_format_percent(self) -> None:
        assert format_percent(None) == "—"
        assert format_percent(1.234) == "1.23%"

    def test_format_timestamp(self) -> None:
        ts = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
        assert format_timestamp(None) == "—"
        assert format_timestamp(ts) == "2026-08-05 12:00:00 UTC"


class TestSettingsRedaction:
    """T11: Settings view redacts secret-looking keys."""

    def test_settings_redaction_helper(self) -> None:
        from dashboard.pages.settings import _redact_key

        assert _redact_key("api_key") == "[REDACTED]"
        assert _redact_key("dashboard.host") == "dashboard.host"


class TestAppEntry:
    """Import and resolve helpers."""

    def test_resolve_page_fallback(self) -> None:
        page = resolve_page("unknown")
        assert page.page_id == "home"

    def test_version_constants(self) -> None:
        assert DASHBOARD_VERSION == "1.0.0"
