"""Read-only live session adapter for DashboardFacade.

Maps already-running platform components into the soft-read accessors
consumed by ``DashboardIntegrationFacade``. Does not evaluate strategies,
place orders, open websockets, or construct new engines.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Callable, Mapping

from dashboard.facade import DashboardBackendFacade
from dashboard.view_models import PLACEHOLDER

_logger = logging.getLogger("dashboard.live_session_adapter")

INDEX_UNDERLYINGS: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")
INDIA_VIX_SYMBOL: str = "INDIA VIX"

_OPTION_CHAIN_COLUMNS: tuple[str, ...] = (
    "strike",
    "type",
    "ltp",
    "bid",
    "ask",
    "oi",
    "oi_change",
    "volume",
    "iv",
    "delta",
    "gamma",
    "theta",
    "vega",
)

# Real Zerodha/NSE quote keys for chart candle fetches — the same identity
# format the broker's own historical-data REST call expects.
_UNDERLYING_QUOTE_KEYS: Mapping[str, str] = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:BANKNIFTY",
    "FINNIFTY": "NSE:FINNIFTY",
    "SENSEX": "BSE:SENSEX",
}
_CANDLE_INTERVAL = "5minute"
_CANDLE_LOOKBACK = timedelta(days=5)

_LIVE_HANDLES_LOCK = threading.RLock()
_REGISTERED_HANDLES: DashboardLiveHandles | None = None


def _utc_now() -> datetime:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc)


def _attr(obj: object | None, name: str, default: object = None) -> object:
    """Safely read an attribute."""
    if obj is None:
        return default
    return getattr(obj, name, default)


def _field(obj: object | None, *names: str, default: object = None) -> object:
    """Read the first available attribute or mapping key."""
    if obj is None:
        return default
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            value = obj[name]
            if value is not None:
                return value
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _enum_value(value: object | None) -> str:
    """Normalize enum/status-like values to lowercase strings."""
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw).strip().lower()


def _display(value: object | None, default: str) -> str:
    """Format a value as a stripped string or default."""
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


@dataclass
class DashboardLiveHandles:
    """Optional live component handles injected into the dashboard adapter.

    All fields are optional. Missing handles preserve offline/placeholder
    behavior for that domain. Handles must already be constructed by the
    host process — this adapter never builds engines or brokers.

    Attributes:
        integration_session: Optional session providing ``get_health`` /
            ``get_runtime_state``.
        market_streaming: ``MarketDataStreamingEngine``-like pull API.
        kite_websocket: ``KiteWebSocketClient``-like status/health API.
        paper_runner: ``PaperTradingRunner``-like read API.
        evaluation_bundle_provider: Returns a cached evaluation bundle or
            ``None`` (never evaluates).
        market_regime_provider: Returns a regime snapshot/label or ``None``.
        scoring_framework: ``StrategyScoringFramework``-like observability
            API (``health`` / ``statistics`` only).
        ai_decision_loop: ``AIDecisionLoop``-like read API
            (``get_latest_decision`` / ``get_health`` / ``config``) used
            only for already-computed reasoning text and cycle timing —
            never invoked to run a cycle.
    """

    integration_session: object | None = None
    market_streaming: object | None = None
    kite_websocket: object | None = None
    paper_runner: object | None = None
    evaluation_bundle_provider: Callable[[], object | None] | None = None
    market_regime_provider: Callable[[], object | None] | None = None
    scoring_framework: object | None = None
    ai_decision_loop: object | None = None

    def has_any_live_source(self) -> bool:
        """Return ``True`` when at least one live handle is present."""
        return any(
            (
                self.integration_session is not None,
                self.market_streaming is not None,
                self.kite_websocket is not None,
                self.paper_runner is not None,
                self.evaluation_bundle_provider is not None,
                self.market_regime_provider is not None,
                self.scoring_framework is not None,
                self.ai_decision_loop is not None,
            )
        )


@dataclass
class _CachedEvaluation:
    """Process-local optional evaluation bundle cache."""

    bundle: object | None = None


class DashboardLiveSessionAdapter:
    """Thread-safe read-only adapter implementing facade session soft-reads.

    Args:
        handles: Injected live component handles.
        clock: Injectable clock for deterministic tests.
    """

    def __init__(
        self,
        handles: DashboardLiveHandles | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            handles: Optional live handles; defaults to empty (offline-like).
            clock: Injectable clock.
        """
        self._handles = handles or DashboardLiveHandles()
        self._clock = clock or _utc_now
        self._lock = threading.RLock()
        self._evaluation_cache = _CachedEvaluation()
        # Real Zerodha option-chain rows carry today's open interest but no
        # baseline to diff against — OI change is derived here by comparing
        # each poll's OI to the previous poll's, per instrument, entirely
        # from real observed values (never fabricated; empty until a second
        # real observation exists).
        self._previous_oi: dict[str, int] = {}
        # Real equity readings captured on every poll since this dashboard
        # process started (never backfilled/estimated) — the paper ledger
        # itself has no historical time series, so this is the only honest
        # source for an equity/drawdown curve.
        self._equity_history: list[tuple[datetime, float]] = []
        self._equity_history_max: int = 1000

    @property
    def handles(self) -> DashboardLiveHandles:
        """Return the injected live handles."""
        return self._handles

    def set_evaluation_bundle(self, bundle: object | None) -> None:
        """Store a precomputed evaluation bundle for strategy display.

        Args:
            bundle: Cached evaluation bundle-like object, or ``None`` to
                clear. Does not run evaluation.
        """
        with self._lock:
            self._evaluation_cache.bundle = bundle

    def get_health(self) -> object:
        """Return health for facade connectivity mapping.

        Prefers the integration session health report. Otherwise synthesizes
        a disconnected/running status from websocket/streaming health.
        """
        with self._lock:
            session = self._handles.integration_session
            if session is not None and callable(getattr(session, "get_health", None)):
                try:
                    return session.get_health()
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("live adapter session.get_health failed: %s", exc)

            connected = self._is_market_connected()
            scoring_state = self._scoring_health_state()
            if connected and scoring_state in {"", "healthy", "disabled"}:
                overall = "healthy"
                state = "running"
            elif connected:
                overall = "degraded"
                state = "degraded"
            else:
                overall = "disconnected"
                state = "stopped"
            return SimpleNamespace(
                session_state=SimpleNamespace(value=state),
                overall_status=SimpleNamespace(value=overall),
                broker_connection=SimpleNamespace(
                    state="connected" if connected else "disconnected",
                    connected=connected,
                ),
                message="live" if connected else "offline",
            )

    def get_runtime_state(self) -> object:
        """Return runtime state for facade system status mapping."""
        with self._lock:
            session = self._handles.integration_session
            if session is not None and callable(
                getattr(session, "get_runtime_state", None)
            ):
                try:
                    return session.get_runtime_state()
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "live adapter session.get_runtime_state failed: %s",
                        exc,
                    )
            return SimpleNamespace(
                execution_mode="PAPER" if self._handles.paper_runner else "ANALYSIS",
                market_status="OPEN" if self._is_market_connected() else "UNKNOWN",
            )

    def get_index_quotes(self) -> tuple[object, ...]:
        """Return Home index quotes from market streaming snapshots.

        Returns:
            Tuple of quote-like namespaces for the four Home symbols.
            Empty when streaming is unavailable.
        """
        with self._lock:
            streaming = self._handles.market_streaming
            if streaming is None or not callable(getattr(streaming, "get_snapshot", None)):
                return ()

            connection = self._connection_label()
            quotes: list[SimpleNamespace] = []
            vix_source: object | None = None

            for symbol in INDEX_UNDERLYINGS:
                snapshot = self._safe_get_snapshot(streaming, symbol)
                if snapshot is None:
                    quotes.append(
                        SimpleNamespace(
                            symbol=symbol,
                            ltp=None,
                            change=None,
                            change_percent=None,
                            timestamp=None,
                            connection_status=(
                                "OFFLINE" if connection == "OFFLINE" else "UNKNOWN"
                            ),
                        )
                    )
                    continue
                underlying = _attr(snapshot, "underlying")
                volatility = _attr(snapshot, "volatility")
                if volatility is not None and vix_source is None:
                    vix_source = volatility
                quotes.append(
                    self._quote_from_underlying(
                        symbol=symbol,
                        underlying=underlying,
                        connection=connection,
                    )
                )

            quotes.append(
                self._quote_from_volatility(
                    volatility=vix_source,
                    connection=connection,
                )
            )
            return tuple(quotes)

    def get_home_market_indices(self) -> object:
        """Return a home-market payload with ``indices`` for the facade."""
        with self._lock:
            return SimpleNamespace(indices=self.get_index_quotes())

    def get_market_snapshot(self) -> object | None:
        """Return a market page snapshot projection from streaming."""
        with self._lock:
            streaming = self._handles.market_streaming
            if streaming is None:
                return None
            snapshot = None
            selected = "NIFTY"
            for symbol in INDEX_UNDERLYINGS:
                snapshot = self._safe_get_snapshot(streaming, symbol)
                if snapshot is not None:
                    selected = symbol
                    break
            if snapshot is None:
                return None
            underlying = _attr(snapshot, "underlying")
            option_chain = _attr(snapshot, "option_chain")
            metadata = _attr(option_chain, "metadata")
            atm_strike = _attr(metadata, "atm_strike")
            return SimpleNamespace(
                underlyings=list(INDEX_UNDERLYINGS),
                selected_underlying=_display(_attr(underlying, "symbol"), selected),
                ltp=_attr(underlying, "last_price"),
                change=_attr(underlying, "change"),
                volume=_attr(underlying, "volume"),
                indices={quote.symbol: quote for quote in self.get_index_quotes()},
                option_chain_columns=_OPTION_CHAIN_COLUMNS,
                option_chain_rows=self._option_chain_rows(option_chain),
                atm_strike=_display(atm_strike, PLACEHOLDER),
            )

    def _option_chain_rows(self, option_chain: object | None) -> tuple[tuple[str, ...], ...]:
        """Build real option-chain display rows from live contract quotes.

        Every value is read directly off ``OptionContractSnapshot``
        (strike/type/ltp/bid/ask/oi/volume/iv/delta/gamma/theta/vega) —
        never estimated. ``oi_change`` is the one derived value: this
        poll's open interest minus the previous poll's for the same
        instrument, both real observations: it just diffs two real
        readings rather than fabricating a baseline.
        """
        contracts = _attr(option_chain, "contracts") or ()
        rows: list[tuple[str, ...]] = []
        current_oi: dict[str, int] = {}
        for contract in contracts:
            instrument_key = str(_attr(contract, "tradingsymbol", default=""))
            open_interest = _attr(contract, "open_interest")
            if isinstance(open_interest, (int, float)):
                current_oi[instrument_key] = int(open_interest)
                previous = self._previous_oi.get(instrument_key)
                oi_change = str(int(open_interest) - previous) if previous is not None else PLACEHOLDER
            else:
                oi_change = PLACEHOLDER
            option_type = _attr(contract, "option_type")
            rows.append(
                (
                    _display(_attr(contract, "strike"), PLACEHOLDER),
                    _display(getattr(option_type, "value", option_type), PLACEHOLDER),
                    _display(_attr(contract, "ltp"), PLACEHOLDER),
                    _display(_attr(contract, "bid"), PLACEHOLDER),
                    _display(_attr(contract, "ask"), PLACEHOLDER),
                    _display(open_interest, PLACEHOLDER),
                    oi_change,
                    _display(_attr(contract, "volume"), PLACEHOLDER),
                    _display(_attr(contract, "iv"), PLACEHOLDER),
                    _display(_attr(contract, "delta"), PLACEHOLDER),
                    _display(_attr(contract, "gamma"), PLACEHOLDER),
                    _display(_attr(contract, "theta"), PLACEHOLDER),
                    _display(_attr(contract, "vega"), PLACEHOLDER),
                )
            )
        self._previous_oi = current_oi
        return tuple(rows)

    def get_ai_panel(self) -> object | None:
        """Return AI reasoning and next-evaluation countdown from the decision loop.

        Reads only already-computed values via ``AIDecisionLoop``'s own
        public accessors (``get_latest_decision`` / ``get_health`` /
        ``config``) — never triggers a cycle.

        Returns:
            ``None`` when no ``ai_decision_loop`` handle is registered or
            no decision has run yet; otherwise a namespace with
            ``reasons``, ``decision_status``, ``strategy_id``,
            ``next_evaluation_seconds`` (``None`` when unknown), and
            ``next_evaluation_display``.
        """
        with self._lock:
            loop = self._handles.ai_decision_loop
            if loop is None:
                return None
            get_latest_decision = getattr(loop, "get_latest_decision", None)
            decision = get_latest_decision() if callable(get_latest_decision) else None
            if decision is None:
                return None

            reasons = tuple(_attr(decision, "reasons", default=()) or ())
            sizing_reason = _attr(decision, "sizing_reason")
            if sizing_reason and sizing_reason not in reasons:
                reasons = reasons + (str(sizing_reason),)

            next_eval_seconds: float | None = None
            get_health = getattr(loop, "get_health", None)
            health = get_health() if callable(get_health) else None
            config = getattr(loop, "config", None)
            interval_seconds = _attr(config, "interval_seconds")
            last_cycle_at = _attr(health, "last_cycle_at")
            if isinstance(interval_seconds, (int, float)) and isinstance(last_cycle_at, datetime):
                elapsed = (self._clock() - last_cycle_at).total_seconds()
                next_eval_seconds = max(0.0, float(interval_seconds) - elapsed)

            next_eval_display = (
                f"{int(next_eval_seconds)}s" if next_eval_seconds is not None else PLACEHOLDER
            )

            raw_status = _attr(decision, "status")
            status_value = str(getattr(raw_status, "value", raw_status or "")).strip().lower()
            decision_as_of = _attr(decision, "as_of")

            return SimpleNamespace(
                reasons=reasons,
                decision_status=_display(_attr(decision, "decision_status"), PLACEHOLDER),
                risk_verdict=_display(_attr(decision, "risk_verdict"), PLACEHOLDER),
                strategy_id=_display(_attr(decision, "strategy_id"), PLACEHOLDER),
                next_evaluation_seconds=next_eval_seconds,
                next_evaluation_display=next_eval_display,
                # Real decision-cycle fields carried through for chart signal
                # markers — never computed or inferred, only relayed.
                signal_active=status_value == "trade_candidate",
                as_of=decision_as_of if isinstance(decision_as_of, datetime) else None,
                underlying=_display(_attr(decision, "underlying"), PLACEHOLDER),
            )

    def get_underlying_candles(self, underlying: str) -> object | None:
        """Return real recent OHLCV candles for ``underlying`` for charting.

        Delegates to the already-connected ``market_streaming`` engine's own
        ``fetch_historical_candles`` — the same broker link it already uses
        for live snapshots. Never opens a new broker connection and never
        estimates or backfills a candle.

        Returns:
            ``None`` when no streaming handle is registered, the underlying
            is unrecognized, or the fetch fails; otherwise a namespace with
            ``underlying``, ``interval``, and raw broker ``candles`` rows.
        """
        with self._lock:
            engine = self._handles.market_streaming
            if engine is None:
                return None
            quote_key = _UNDERLYING_QUOTE_KEYS.get(underlying.strip().upper())
            if quote_key is None:
                return None
            fetch = getattr(engine, "fetch_historical_candles", None)
            if not callable(fetch):
                return None
            now = self._clock()
            request = SimpleNamespace(
                instrument_key=quote_key,
                interval=_CANDLE_INTERVAL,
                from_ts=now - _CANDLE_LOOKBACK,
                to_ts=now,
                continuous=False,
                correlation_id=None,
            )
            try:
                result = fetch(request)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("live adapter candle fetch failed: %s", exc)
                return None
            rows = _attr(result, "candles", default=()) or ()
            return SimpleNamespace(
                underlying=underlying,
                interval=_CANDLE_INTERVAL,
                candles=tuple(rows),
            )

    def get_strategy_evaluation_summary(self) -> object | None:
        """Return a cached evaluation bundle/summary without evaluating."""
        with self._lock:
            bundle = self._resolve_evaluation_bundle()
            if bundle is None:
                return None
            regime = self._resolve_regime_label(bundle)
            return SimpleNamespace(
                market_regime=regime,
                active_strategy=_field(
                    bundle,
                    "active_strategy",
                    "selected_strategy",
                    default=_attr(_attr(bundle, "summary"), "top_strategy_id"),
                ),
                confidence_score=self._top_confidence(bundle),
                evaluated_at=_field(bundle, "evaluated_at", "as_of"),
                summary=_attr(bundle, "summary"),
                reports=_attr(bundle, "ranked_reports") or _attr(bundle, "reports") or (),
                strategies=_attr(bundle, "strategies")
                or _attr(bundle, "ranked_reports")
                or _attr(bundle, "reports")
                or (),
            )

    def get_strategy_status(self) -> object | None:
        """Alias used by the facade for strategy monitor mapping."""
        return self.get_strategy_evaluation_summary()

    def get_market_regime(self) -> object | None:
        """Return market regime from provider or evaluation metadata."""
        with self._lock:
            label = self._resolve_regime_label(self._resolve_evaluation_bundle())
            if not label or label == PLACEHOLDER:
                return None
            return SimpleNamespace(regime=label, market_regime=label, label=label)

    def get_paper_trading_snapshot(self) -> object | None:
        """Return paper capital/positions snapshot from the runner (read-only)."""
        with self._lock:
            runner = self._handles.paper_runner
            if runner is None:
                return None
            try:
                portfolio = None
                if callable(getattr(runner, "get_portfolio_view", None)):
                    portfolio = runner.get_portfolio_view()
                capital = None
                if callable(getattr(runner, "get_capital_snapshot", None)):
                    capital = runner.get_capital_snapshot()
                book = None
                if callable(getattr(runner, "get_position_book", None)):
                    book = runner.get_position_book()
            except Exception as exc:  # noqa: BLE001
                _logger.warning("live adapter paper read failed: %s", exc)
                return None

            if portfolio is None and capital is None and book is None:
                return None

            positions: object = ()
            if book is not None:
                positions = _attr(book, "positions", ()) or ()
            elif portfolio is not None:
                nested = _attr(portfolio, "positions")
                positions = _attr(nested, "positions", nested) or ()

            cash = _field(capital, "cash", "available_cash")
            if cash is None and portfolio is not None:
                cash = _field(_attr(portfolio, "capital"), "cash", "available_cash")
            unrealized_pnl = _field(portfolio, "total_unrealized_pnl", "unrealized_pnl")
            starting_cash = _field(capital, "starting_cash")

            equity_series, drawdown_series = self._record_and_build_equity_curve(
                cash=cash, unrealized_pnl=unrealized_pnl
            )
            total_equity = equity_series[-1][1] if equity_series else None
            roi = None
            if total_equity is not None and isinstance(starting_cash, (int, float, Decimal)):
                base = float(starting_cash)
                if base:
                    roi = ((total_equity - base) / base) * 100.0

            return SimpleNamespace(
                available_cash=cash,
                cash=cash,
                virtual_cash=cash,
                capital=capital,
                portfolio_view=portfolio,
                capital_used=_field(capital, "reserved_margin_hint", "capital_used")
                or _field(portfolio, "gross_notional"),
                total_equity=total_equity,
                todays_pnl=_field(portfolio, "todays_pnl", "daily_pnl"),
                realized_pnl=_field(portfolio, "total_realized_pnl", "realized_pnl")
                or _field(capital, "cumulative_realized_pnl"),
                unrealized_pnl=unrealized_pnl,
                positions=positions,
                equity_series=equity_series,
                drawdown_series=drawdown_series,
                roi=roi,
            )

    def _record_and_build_equity_curve(
        self, *, cash: object, unrealized_pnl: object
    ) -> tuple[tuple[tuple[str, float], ...], tuple[tuple[str, float], ...]]:
        """Append this poll's real equity reading and derive the curves.

        Equity is ``cash + unrealized_pnl`` — both already-computed real
        ledger values, never estimated. Drawdown is the running peak minus
        the current reading over the same real, captured-since-startup
        history (empty at process start; grows one point per poll).
        """
        if not isinstance(cash, (int, float, Decimal)) or not isinstance(
            unrealized_pnl, (int, float, Decimal)
        ):
            return (), ()
        equity = float(cash) + float(unrealized_pnl)
        now = self._clock()
        self._equity_history.append((now, equity))
        if len(self._equity_history) > self._equity_history_max:
            self._equity_history = self._equity_history[-self._equity_history_max :]

        equity_series = tuple(
            (timestamp.isoformat(), value) for timestamp, value in self._equity_history
        )
        peak = float("-inf")
        drawdown_points: list[tuple[str, float]] = []
        for timestamp, value in self._equity_history:
            peak = max(peak, value)
            drawdown_points.append((timestamp.isoformat(), value - peak))
        return equity_series, tuple(drawdown_points)

    def get_paper_positions(self) -> object | None:
        """Facade companion alias for paper positions."""
        return self.get_paper_trading_snapshot()

    def get_paper_trading_ledger(self) -> object | None:
        """Facade companion alias for paper ledger assembly."""
        return self.get_paper_trading_snapshot()

    def get_order_book(self) -> object:
        """Return an empty order book (orders remain soft-degraded)."""
        return SimpleNamespace(orders=())

    def _is_market_connected(self) -> bool:
        """Infer whether market connectivity appears live."""
        ws = self._handles.kite_websocket
        if ws is not None and callable(getattr(ws, "get_status", None)):
            try:
                status = _enum_value(ws.get_status())
                if status in {"connected", "authenticated", "live"}:
                    return True
                if status in {"disconnected", "error", "failed"}:
                    return False
            except Exception:  # noqa: BLE001
                pass
        streaming = self._handles.market_streaming
        if streaming is not None and callable(getattr(streaming, "get_health", None)):
            try:
                health = streaming.get_health()
                status = _enum_value(
                    _field(health, "status", "overall_status", "lifecycle_state")
                )
                if status in {"healthy", "running", "live", "connected"}:
                    return True
            except Exception:  # noqa: BLE001
                pass
        if streaming is not None and callable(getattr(streaming, "get_snapshot", None)):
            for symbol in INDEX_UNDERLYINGS:
                if self._safe_get_snapshot(streaming, symbol) is not None:
                    return True
        return False

    def _connection_label(self) -> str:
        """Return LIVE / OFFLINE for quote cards."""
        if not self._is_market_connected():
            return "OFFLINE"
        return "LIVE"

    def _scoring_health_state(self) -> str:
        """Read scoring framework health without scoring."""
        framework = self._handles.scoring_framework
        if framework is None or not callable(getattr(framework, "health", None)):
            return ""
        try:
            report = framework.health()
            return _enum_value(_field(report, "health", "state", "status"))
        except Exception as exc:  # noqa: BLE001
            _logger.warning("scoring framework health failed: %s", exc)
            return "degraded"

    def _safe_get_snapshot(self, streaming: object, symbol: str) -> object | None:
        """Pull a snapshot for ``symbol`` without raising."""
        try:
            return streaming.get_snapshot(symbol)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("streaming.get_snapshot(%s) failed: %s", symbol, exc)
            return None

    def _quote_from_underlying(
        self,
        *,
        symbol: str,
        underlying: object | None,
        connection: str,
    ) -> SimpleNamespace:
        """Map an underlying snapshot to a facade quote namespace."""
        ltp = _field(underlying, "last_price", "ltp")
        change = _field(underlying, "change", "net_change")
        change_pct = _field(underlying, "change_percent", "pchange")
        timestamp = _field(underlying, "quote_timestamp", "timestamp", "as_of")
        status = connection
        if ltp is None and connection != "OFFLINE":
            status = "UNKNOWN"
        return SimpleNamespace(
            symbol=symbol,
            ltp=ltp,
            change=change,
            change_percent=change_pct,
            timestamp=timestamp,
            connection_status=status,
            is_stale=False,
        )

    def _quote_from_volatility(
        self,
        *,
        volatility: object | None,
        connection: str,
    ) -> SimpleNamespace:
        """Map volatility snapshot to INDIA VIX quote."""
        if volatility is None:
            return SimpleNamespace(
                symbol=INDIA_VIX_SYMBOL,
                ltp=None,
                change=None,
                change_percent=None,
                timestamp=None,
                connection_status="OFFLINE" if connection == "OFFLINE" else "UNKNOWN",
            )
        return SimpleNamespace(
            symbol=INDIA_VIX_SYMBOL,
            ltp=_field(volatility, "last_price", "ltp"),
            change=_field(volatility, "change"),
            change_percent=_field(volatility, "change_percent", "pchange"),
            timestamp=_field(volatility, "quote_timestamp", "timestamp"),
            connection_status=connection if connection != "OFFLINE" else "OFFLINE",
            is_stale=False,
        )

    def _resolve_evaluation_bundle(self) -> object | None:
        """Resolve cached evaluation bundle from provider or local cache."""
        provider = self._handles.evaluation_bundle_provider
        if provider is not None:
            try:
                bundle = provider()
                if bundle is not None:
                    return bundle
            except Exception as exc:  # noqa: BLE001
                _logger.warning("evaluation_bundle_provider failed: %s", exc)
        return self._evaluation_cache.bundle

    def _resolve_regime_label(self, bundle: object | None) -> str:
        """Resolve market regime label from provider or bundle metadata."""
        provider = self._handles.market_regime_provider
        if provider is not None:
            try:
                regime = provider()
                label = _display(
                    _field(
                        regime,
                        "regime",
                        "market_regime",
                        "label",
                        "name",
                        default=regime,
                    ),
                    "",
                )
                if label:
                    return label
            except Exception as exc:  # noqa: BLE001
                _logger.warning("market_regime_provider failed: %s", exc)
        if bundle is None:
            return ""
        metadata = _attr(bundle, "metadata")
        return _display(
            _field(bundle, "market_regime", "regime")
            or _field(metadata, "market_regime", "regime"),
            "",
        )

    def _top_confidence(self, bundle: object) -> object | None:
        """Extract top-report confidence scalar for header display."""
        summary = _attr(bundle, "summary")
        reports = _attr(bundle, "ranked_reports") or _attr(bundle, "reports") or ()
        top_id = _attr(summary, "top_strategy_id")
        try:
            for report in reports:
                strategy_id = _field(report, "strategy_id", "family", "strategy_family")
                if top_id is None or str(strategy_id) == str(top_id):
                    confidence = _attr(report, "confidence")
                    overall = _field(confidence, "overall_score", "score")
                    if overall is not None:
                        return overall
                    return _field(report, "confidence_score", "ranking_score")
        except TypeError:
            return None
        return None


def register_live_handles(handles: DashboardLiveHandles) -> None:
    """Register process-level live handles for the Streamlit app factory.

    Args:
        handles: Live component handles constructed by the host process.
    """
    global _REGISTERED_HANDLES
    with _LIVE_HANDLES_LOCK:
        _REGISTERED_HANDLES = handles


def clear_live_handles() -> None:
    """Clear registered live handles (tests / shutdown)."""
    global _REGISTERED_HANDLES
    with _LIVE_HANDLES_LOCK:
        _REGISTERED_HANDLES = None


def get_registered_live_handles() -> DashboardLiveHandles | None:
    """Return currently registered live handles, if any."""
    with _LIVE_HANDLES_LOCK:
        return _REGISTERED_HANDLES


def build_default_presentation_facade() -> DashboardBackendFacade:
    """Build the presentation facade, preserving offline by default.

    When live handles are registered and contain at least one source, wraps
    them in ``DashboardLiveSessionAdapter``. Otherwise returns an offline
    facade (``session=None``).

    Returns:
        Presentation facade for Streamlit pages.
    """
    from dashboard.dashboard_facade import DashboardFacade

    handles = get_registered_live_handles()
    if handles is None or not handles.has_any_live_source():
        return DashboardFacade(session=None).as_presentation_facade()
    adapter = DashboardLiveSessionAdapter(handles)
    return DashboardFacade(session=adapter).as_presentation_facade()


def build_live_presentation_facade(
    handles: DashboardLiveHandles,
    *,
    clock: Callable[[], datetime] | None = None,
) -> DashboardBackendFacade:
    """Build a presentation facade bound to explicit live handles.

    Args:
        handles: Live handles to expose through the adapter.
        clock: Injectable clock for tests.

    Returns:
        Presentation facade backed by ``DashboardLiveSessionAdapter``.
    """
    from dashboard.dashboard_facade import DashboardFacade

    adapter = DashboardLiveSessionAdapter(handles, clock=clock)
    return DashboardFacade(session=adapter, clock=clock).as_presentation_facade()


__all__ = [
    "DashboardLiveHandles",
    "DashboardLiveSessionAdapter",
    "INDEX_UNDERLYINGS",
    "INDIA_VIX_SYMBOL",
    "build_default_presentation_facade",
    "build_live_presentation_facade",
    "clear_live_handles",
    "get_registered_live_handles",
    "register_live_handles",
]
