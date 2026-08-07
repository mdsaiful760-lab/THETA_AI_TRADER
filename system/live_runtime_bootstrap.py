"""Single-startup live runtime bootstrap for THETA AI TRADER.

Composes already-implemented, unmodified modules into the one sequence a
host process needs to go from "nothing running" to "paper trading against
live market data, dashboard attached" — reimplements no authentication,
websocket, streaming, decision, contract-selection, sizing, execution, or
paper-trading logic of its own.

Sequence::

    KiteAuthenticator.restore_session()            (1. login, 2. restore)
        -> BrokerSession
        -> KiteWebSocketClient.connect()             (3. websocket)
        -> KiteBrokerClient.fetch_instruments()       (real instrument catalog,
                                                        via broker.instrument_loader.InstrumentLoader)
        -> WebSocketMarketDataBridge.register_instruments() + .start()
                                                       (4. market data bridge)
        -> MarketDataStreamingEngine.start()           (5. streaming engine)
        -> StrategyRegistry (real strategy plugins)
        -> AIDecisionLoop.start_loop()                 (6. AI decision loop)
        -> PaperTradingRuntime.start()                  (7. paper trading runtime,
                                                            8. dashboard live handles —
                                                            PaperTradingRuntime.start()
                                                            already registers them)

Every stage is wrapped so a failure (no credentials, no network, no
``kiteconnect`` SDK installed, session expired) is caught, logged, and
recorded on the returned :class:`LiveRuntimeStatus` — never silently
ignored, never papered over with fabricated data. Later stages that do not
strictly depend on a failed earlier stage still start in degraded mode
(e.g. the AI decision loop and paper trading runtime start even without a
live websocket, producing ``NO_SNAPSHOT`` decisions until live data
arrives) so the dashboard always has something real to show.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Final

from broker.base_broker import BrokerSession, Exchange, InstrumentRequest
from broker.instrument_loader import (
    InstrumentCatalog,
    InstrumentLoader,
    InstrumentLoaderError,
    default_instrument_loader_config,
)
from broker.kite_authentication import (
    AuthenticationStatus,
    EnvironmentProfile,
    KiteAuthenticationConfig,
    KiteAuthenticator,
    default_kite_authentication_config,
)
from broker.kite_websocket import KiteWebSocketClient, KiteWebSocketConfig, WebSocketConnectionStatus
from broker.market_data_bridge import WebSocketMarketDataBridge
from broker.market_data_streaming import (
    InstrumentDescriptor,
    InstrumentRole,
    MarketDataStreamingConfig,
    MarketDataStreamingEngine,
    resolve_instrument_role,
)
from broker.zerodha._kite_policy import KiteBrokerPolicy
from broker.zerodha.kite_broker import KiteBrokerClient
from dashboard.live_session_adapter import get_registered_live_handles
from decision.ai_decision_loop import AiDecisionLoopConfig, AIDecisionLoop
from strategy.bear_call_spread_strategy import BearCallSpreadStrategy, default_bear_call_spread_configuration
from strategy.bull_put_spread_strategy import BullPutSpreadStrategy, default_bull_put_spread_configuration
from strategy.iron_condor_strategy import IronCondorStrategy, default_iron_condor_configuration
from strategy.registry import StrategyRegistry
from strategy.short_strangle_strategy import ShortStrangleStrategy, default_short_strangle_configuration
from strategy.strategy_scoring_framework import ScoringFrameworkConfig, StrategyScoringFramework
from system.paper_trading_runtime import PaperTradingRuntime

_LOGGER = logging.getLogger("system.live_runtime_bootstrap")


def _utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


class _KiteInstrumentMasterClient:
    """Adapts ``KiteBrokerClient.fetch_instruments`` to the
    ``InstrumentMasterClient`` protocol ``InstrumentLoader`` expects.

    Pure signature/shape adapter — forwards to the broker client's own
    existing, unmodified instrument-fetch capability; fetches no data and
    computes nothing itself.
    """

    def __init__(self, broker_client: KiteBrokerClient) -> None:
        self._broker_client = broker_client

    def fetch_instrument_rows(self, *, exchange: str):
        request = InstrumentRequest(exchange=Exchange(exchange.lower()))
        return self._broker_client.fetch_instruments(request)


# Real Zerodha NSE tradingsymbols for the index/volatility instruments the
# dashboard's Home page reads prices from. SENSEX is intentionally excluded
# (its spot/option instruments trade on BSE/BFO, not NSE/NFO — out of the
# currently required scope).
_INDEX_SPOT_TRADINGSYMBOLS: Final[dict[str, str]] = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
}
_VOLATILITY_INDEX_TRADINGSYMBOL: Final[str] = "INDIA VIX"
_ZERO_LOT_INDEX_TRADINGSYMBOLS: Final[frozenset[str]] = frozenset(
    set(_INDEX_SPOT_TRADINGSYMBOLS) | {_VOLATILITY_INDEX_TRADINGSYMBOL}
)


def _with_index_lot_size_patched(
    rows: Sequence[Mapping[str, object]]
) -> list[dict[str, object]]:
    """Patch lot_size and tick_size for the NIFTY 50 / NIFTY BANK / INDIA VIX rows only.

    Zerodha's real NSE instrument dump reports ``lot_size=0`` AND
    ``tick_size=0.0`` for these three rows — they are index/volatility
    reference instruments, never tradable contracts, so the broker never
    assigns them a lot or tick size. ``InstrumentLoader._normalize_record``
    (broker/instrument_loader.py) requires ``lot_size >= 1`` and
    ``tick_size > 0`` and silently drops any row that fails either check,
    which otherwise empties the catalog of the exact three rows the
    dashboard's live index tickers read. Patched values are never used for
    order sizing or pricing — these descriptors are display-only SPOT/
    VOLATILITY_INDEX quotes, never option legs.
    """
    patched: list[dict[str, object]] = []
    for row in rows:
        row = dict(row)
        symbol = str(row.get("tradingsymbol", "")).strip()
        if symbol in _ZERO_LOT_INDEX_TRADINGSYMBOLS:
            lot_size = row.get("lot_size")
            tick_size = row.get("tick_size")
            # Real broker responses give these as numeric 0/0.0; some
            # raw/CSV or test-fixture sources give them as strings ("0") —
            # a plain falsy check misses the string form, since "0" is
            # truthy in Python.
            if lot_size in (None, "", 0, "0", 0.0):
                row["lot_size"] = 1
            if tick_size in (None, "", 0, "0", 0.0):
                row["tick_size"] = 0.05
        patched.append(row)
    return patched


def _index_and_volatility_descriptors(
    catalog: InstrumentCatalog,
    *,
    spot_underlyings: Sequence[str],
    volatility_underlying: str,
) -> tuple[InstrumentDescriptor, ...]:
    """Build SPOT (index) and VOLATILITY_INDEX descriptors from a real NSE catalog.

    Real Zerodha rows for these symbols carry ``instrument_type="EQ"`` (not
    ``"INDEX"``), so ``instrument_role`` is set explicitly here rather than
    re-derived from ``instrument_type`` — the same reasoning
    ``_nearest_expiry_option_descriptors`` already applies for CE/PE legs.
    INDIA VIX attaches to ``volatility_underlying`` (the primary trading
    underlying's own snapshot): ``MarketDataStreamingEngine`` has no
    separate "INDIA VIX" underlying slot — matching this codebase's own
    test fixtures, which already embed a ``VolatilitySnapshot`` inside the
    NIFTY ``MarketSnapshot`` rather than giving VIX its own snapshot.
    """
    wanted_spot = {
        symbol: canonical
        for symbol, canonical in _INDEX_SPOT_TRADINGSYMBOLS.items()
        if canonical in spot_underlyings
    }
    descriptors: list[InstrumentDescriptor] = []
    for record in catalog.records:
        symbol = record.tradingsymbol.strip()
        if symbol in wanted_spot:
            descriptors.append(
                InstrumentDescriptor(
                    instrument_token=record.instrument_token,
                    underlying=wanted_spot[symbol],
                    quote_key=record.quote_key,
                    exchange=record.exchange,
                    tradingsymbol=record.tradingsymbol,
                    instrument_kind="INDEX",
                    instrument_role=InstrumentRole.SPOT,
                )
            )
        elif symbol == _VOLATILITY_INDEX_TRADINGSYMBOL:
            descriptors.append(
                InstrumentDescriptor(
                    instrument_token=record.instrument_token,
                    underlying=volatility_underlying,
                    quote_key=record.quote_key,
                    exchange=record.exchange,
                    tradingsymbol=record.tradingsymbol,
                    instrument_kind="VIX",
                    instrument_role=InstrumentRole.VOLATILITY_INDEX,
                )
            )
    return tuple(descriptors)


def _nearest_expiry_option_descriptors(
    catalog: InstrumentCatalog, *, underlying: str, exchange: str = "NFO"
) -> tuple[InstrumentDescriptor, ...]:
    """Filter an already-loaded real catalog to one underlying's nearest-expiry option chain.

    Pure filtering over already-fetched, real broker records — selects the
    soonest non-expired expiry present for ``underlying`` and returns every
    CE/PE contract at that expiry; fabricates no strike, token, or expiry.
    """
    underlying_norm = underlying.strip().upper()
    candidates = [
        record
        for record in catalog.records
        if not record.is_expired
        and record.underlying.strip().upper() == underlying_norm
        and record.exchange.strip().upper() == exchange.strip().upper()
        and record.option_type in ("CE", "PE")
        and record.expiry
    ]
    if not candidates:
        return ()
    nearest_expiry = min(record.expiry for record in candidates)
    selected = [record for record in candidates if record.expiry == nearest_expiry]
    return tuple(
        InstrumentDescriptor(
            instrument_token=record.instrument_token,
            underlying=record.underlying,
            quote_key=record.quote_key,
            exchange=record.exchange,
            tradingsymbol=record.tradingsymbol,
            instrument_kind=record.instrument_type,
            # broker.instrument_loader.InstrumentRole/UnderlyingSupportTier
            # are distinct enum classes from market_data_streaming's own
            # (same names, different identity) — instrument_role is
            # re-resolved via market_data_streaming's own resolver instead
            # of passing the loader's incompatible enum instance through;
            # support_tier (observability-only) is left at its None default.
            instrument_role=resolve_instrument_role(record.instrument_type),
            strike=record.strike,
            option_type=record.option_type,
            expiry=record.expiry,
            lot_size=record.lot_size,
            tick_size=record.tick_size,
        )
        for record in selected
    )


def _build_strategy_registry() -> StrategyRegistry:
    """Register every currently implemented real strategy plugin."""
    scoring_framework = StrategyScoringFramework(ScoringFrameworkConfig())
    registry = StrategyRegistry()
    registry.register(
        ShortStrangleStrategy(default_short_strangle_configuration(), scoring_framework)
    )
    registry.register(
        IronCondorStrategy(default_iron_condor_configuration(), scoring_framework)
    )
    registry.register(
        BullPutSpreadStrategy(default_bull_put_spread_configuration(), scoring_framework)
    )
    registry.register(
        BearCallSpreadStrategy(default_bear_call_spread_configuration(), scoring_framework)
    )
    return registry


@dataclass
class LiveRuntimeStatus:
    """The startup verification checklist — one flag + note per stage."""

    authenticated: bool = False
    authentication_note: str = "not attempted"
    websocket_connected: bool = False
    websocket_note: str = "not attempted"
    instruments_loaded: int = 0
    instruments_note: str = "not attempted"
    bridge_running: bool = False
    bridge_note: str = "not attempted"
    streaming_running: bool = False
    streaming_note: str = "not attempted"
    ai_loop_running: bool = False
    ai_loop_note: str = "not attempted"
    paper_runtime_running: bool = False
    paper_runtime_note: str = "not attempted"
    dashboard_registered: bool = False
    dashboard_note: str = "not attempted"

    def summary_lines(self) -> tuple[str, ...]:
        """Render the ✓/✗ checklist the milestone asks for."""

        def mark(ok: bool) -> str:
            return "✓" if ok else "✗"

        return (
            f"{mark(self.authenticated)} Zerodha session: {self.authentication_note}",
            f"{mark(self.websocket_connected)} WebSocket connected: {self.websocket_note}",
            f"{mark(self.instruments_loaded > 0)} Instruments registered: {self.instruments_note}",
            f"{mark(self.bridge_running)} MarketDataBridge running: {self.bridge_note}",
            f"{mark(self.streaming_running)} Streaming engine running: {self.streaming_note}",
            f"{mark(self.ai_loop_running)} AI decision loop running: {self.ai_loop_note}",
            f"{mark(self.paper_runtime_running)} PaperTradingRuntime running: {self.paper_runtime_note}",
            f"{mark(self.dashboard_registered)} Dashboard receives live runtime: {self.dashboard_note}",
        )


@dataclass
class LiveRuntimeHandles:
    """Every component this bootstrap constructed, plus the status checklist."""

    status: LiveRuntimeStatus
    authenticator: KiteAuthenticator | None = None
    broker_session: BrokerSession | None = None
    ws_client: KiteWebSocketClient | None = None
    streaming_engine: MarketDataStreamingEngine | None = None
    bridge: WebSocketMarketDataBridge | None = None
    strategy_registry: StrategyRegistry | None = None
    decision_loop: AIDecisionLoop | None = None
    paper_runtime: PaperTradingRuntime | None = None

    def stop(self) -> None:
        """Best-effort, dependency-ordered shutdown of every started component."""
        if self.paper_runtime is not None:
            try:
                self.paper_runtime.stop()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                _LOGGER.exception("live_runtime_bootstrap.shutdown.paper_runtime_failed")
        if self.streaming_engine is not None:
            try:
                self.streaming_engine.stop()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("live_runtime_bootstrap.shutdown.streaming_engine_failed")
        if self.bridge is not None:
            try:
                self.bridge.stop()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("live_runtime_bootstrap.shutdown.bridge_failed")
        if self.ws_client is not None:
            try:
                self.ws_client.disconnect()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("live_runtime_bootstrap.shutdown.websocket_failed")


def bootstrap_live_runtime(
    *,
    underlying: str = "NIFTY",
    market_data_underlyings: tuple[str, ...] | None = None,
    environment_profile: EnvironmentProfile = EnvironmentProfile.PAPER,
    env: dict[str, str] | None = None,
    authenticator_factory: Callable[[KiteAuthenticationConfig], KiteAuthenticator] | None = None,
    broker_client_factory: Callable[[BrokerSession], KiteBrokerClient] | None = None,
    ws_client_factory: Callable[..., KiteWebSocketClient] | None = None,
    instrument_loader_factory: Callable[..., InstrumentLoader] | None = None,
    ai_loop_interval_seconds: float | None = None,
) -> LiveRuntimeHandles:
    """Run the full single-startup sequence and return every constructed handle.

    Every factory argument is optional and defaults to the real
    constructor; tests inject fakes here instead of needing live
    credentials or network access. Each stage is independently guarded —
    a failure is recorded on ``handles.status`` and logged, and later
    stages that do not strictly require it still start in degraded mode.

    Args:
        underlying: Underlying to trade (index name, e.g. ``"NIFTY"``). Also
            the underlying INDIA VIX's snapshot attaches to.
        market_data_underlyings: Underlyings to subscribe live market data
            for, beyond ``underlying`` (which is always included) — this is
            display/dashboard scope, independent of which underlying
            ``AIDecisionLoop`` trades. Defaults to adding ``"BANKNIFTY"``.
        environment_profile: Auth/websocket/streaming environment profile.
        env: Optional environment mapping override (defaults to
            ``os.environ``) — used only to read already-configured legacy
            credential env var names, never to fabricate one.
        authenticator_factory: Optional ``KiteAuthenticator`` constructor
            override.
        broker_client_factory: Optional ``KiteBrokerClient`` constructor
            override.
        ws_client_factory: Optional ``KiteWebSocketClient`` constructor
            override.
        instrument_loader_factory: Optional ``InstrumentLoader`` constructor
            override.
        ai_loop_interval_seconds: Optional override for the AI decision
            loop's background cycle interval.

    Returns:
        Every constructed component plus the startup verification status.
    """
    status = LiveRuntimeStatus()
    handles = LiveRuntimeHandles(status=status)
    env_map = env if env is not None else dict(os.environ)
    # Market-data (display) scope: always includes the trading underlying,
    # plus BANKNIFTY by default, de-duplicated and order-preserving.
    market_underlyings = tuple(
        dict.fromkeys((underlying,) + (market_data_underlyings or ("BANKNIFTY",)))
    )

    # 1 + 2. Login Zerodha / restore session.
    auth_config = default_kite_authentication_config(environment_profile)
    legacy_access_token = env_map.get(auth_config.legacy_access_token_env) or env_map.get(
        auth_config.access_token_env
    )
    authenticator = (authenticator_factory or KiteAuthenticator)(
        auth_config, env=env_map, access_token=legacy_access_token
    )
    handles.authenticator = authenticator
    try:
        auth_result = authenticator.restore_session()
        if auth_result.status is AuthenticationStatus.AUTHENTICATED and auth_result.broker_session:
            handles.broker_session = auth_result.broker_session
            status.authenticated = True
            status.authentication_note = f"restored (source={auth_result.metadata.token_source.value})"
        else:
            status.authentication_note = f"not authenticated (status={auth_result.status.value})"
            _LOGGER.warning(
                "live_runtime_bootstrap.auth.not_authenticated",
                extra={"status": auth_result.status.value},
            )
    except Exception as exc:  # noqa: BLE001 - never crash bootstrap on auth failure
        status.authentication_note = f"failed: {exc}"
        _LOGGER.warning("live_runtime_bootstrap.auth.failed", extra={"error": str(exc)})

    # 3. Start WebSocket (only possible with a restored broker session).
    if handles.broker_session is not None:
        try:
            ws_config = KiteWebSocketConfig(
                environment_profile=environment_profile,
                enabled_underlyings=market_underlyings,
                max_subscriptions=1000,
            )
            credentials = handles.broker_session.credentials
            ws_client = (ws_client_factory or KiteWebSocketClient)(
                ws_config,
                api_key=str(credentials["api_key"]),
                access_token=str(credentials["access_token"]),
            )
            handles.ws_client = ws_client
            ws_client.connect()
            if ws_client.get_status() in (
                WebSocketConnectionStatus.CONNECTED,
                WebSocketConnectionStatus.CONNECTING,
            ):
                status.websocket_connected = True
                status.websocket_note = ws_client.get_status().value
            else:
                status.websocket_note = f"status={ws_client.get_status().value}"
        except Exception as exc:  # noqa: BLE001
            status.websocket_note = f"failed: {exc}"
            _LOGGER.warning("live_runtime_bootstrap.websocket.failed", extra={"error": str(exc)})
    else:
        status.websocket_note = "skipped: no authenticated broker session"

    # Real instrument catalog (feeds step 4's register_instruments()).
    descriptors: tuple[InstrumentDescriptor, ...] = ()
    if handles.broker_session is not None:
        try:
            broker_client = (broker_client_factory or (lambda s: KiteBrokerClient(s, KiteBrokerPolicy())))(
                handles.broker_session
            )
            broker_client.connect()
            master_client = _KiteInstrumentMasterClient(broker_client)
            loader_config = replace(
                default_instrument_loader_config(
                    environment_profile, enabled_underlyings=market_underlyings
                ),
                # Without this, INDIA VIX's real row (instrument_type="EQ",
                # name="INDIA VIX") normalizes to an "INDIA VIX" underlying
                # that isn't in enabled_underlyings and gets silently
                # dropped by InstrumentLoader's own validation — this makes
                # it survive as a VOLATILITY_INDEX role attached to
                # `underlying` instead (see _index_and_volatility_descriptors).
                volatility_index_map={underlying: _VOLATILITY_INDEX_TRADINGSYMBOL},
            )
            loader = (instrument_loader_factory or InstrumentLoader)(
                loader_config, master_client=master_client
            )
            option_catalog = loader.load_from_broker(exchanges=(Exchange.NFO.value,))
            option_descriptors = tuple(
                descriptor
                for market_underlying in market_underlyings
                for descriptor in _nearest_expiry_option_descriptors(
                    option_catalog, underlying=market_underlying
                )
            )
            index_rows = _with_index_lot_size_patched(
                master_client.fetch_instrument_rows(exchange=Exchange.NSE.value)
            )
            index_catalog = loader.load_from_rows(index_rows)
            index_descriptors = _index_and_volatility_descriptors(
                index_catalog,
                spot_underlyings=market_underlyings,
                volatility_underlying=underlying,
            )
            descriptors = option_descriptors + index_descriptors
            status.instruments_loaded = len(descriptors)
            status.instruments_note = (
                f"{len(option_descriptors)} option contracts + "
                f"{len(index_descriptors)} index/VIX instruments"
                if descriptors
                else "0 instruments resolved"
            )
        except (InstrumentLoaderError, Exception) as exc:  # noqa: BLE001
            status.instruments_note = f"failed: {exc}"
            _LOGGER.warning("live_runtime_bootstrap.instruments.failed", extra={"error": str(exc)})
    else:
        status.instruments_note = "skipped: no authenticated broker session"

    # 5. Start MarketDataStreamingEngine (does not require live credentials to construct/start).
    streaming_engine = MarketDataStreamingEngine(
        MarketDataStreamingConfig(
            enabled_underlyings=market_underlyings, environment_profile=environment_profile
        )
    )
    handles.streaming_engine = streaming_engine
    try:
        streaming_engine.start()
        status.streaming_running = True
        status.streaming_note = streaming_engine.get_status().value
    except Exception as exc:  # noqa: BLE001
        status.streaming_note = f"failed: {exc}"
        _LOGGER.warning("live_runtime_bootstrap.streaming.failed", extra={"error": str(exc)})

    # 4. Register MarketDataBridge (needs both a connected websocket and the streaming engine).
    if handles.ws_client is not None:
        try:
            bridge = WebSocketMarketDataBridge(
                ws_client=handles.ws_client, streaming_engine=streaming_engine
            )
            handles.bridge = bridge
            if descriptors:
                bridge.register_instruments(descriptors)
            bridge.start()
            status.bridge_running = bridge.is_started
            status.bridge_note = (
                "running" if bridge.is_started else "not started"
            ) + ("" if descriptors else " (no instruments registered)")
        except Exception as exc:  # noqa: BLE001
            status.bridge_note = f"failed: {exc}"
            _LOGGER.warning("live_runtime_bootstrap.bridge.failed", extra={"error": str(exc)})
    else:
        status.bridge_note = "skipped: no websocket client"

    # 6. Start AIDecisionLoop — always starts; degrades to NO_SNAPSHOT decisions
    # without live data rather than blocking the rest of the runtime.
    strategy_registry = _build_strategy_registry()
    handles.strategy_registry = strategy_registry
    decision_loop = AIDecisionLoop(
        AiDecisionLoopConfig(underlying=underlying, runner_kind="live_bootstrap"),
        market_data_engine=streaming_engine,
        strategy_registry=strategy_registry,
    )
    handles.decision_loop = decision_loop
    try:
        decision_loop.start_loop(interval_seconds=ai_loop_interval_seconds)
        status.ai_loop_running = True
        status.ai_loop_note = decision_loop.get_state().value
    except Exception as exc:  # noqa: BLE001
        status.ai_loop_note = f"failed: {exc}"
        _LOGGER.warning("live_runtime_bootstrap.ai_loop.failed", extra={"error": str(exc)})

    # 7 + 8. Start PaperTradingRuntime (its own .start() also registers dashboard live handles).
    paper_runtime = PaperTradingRuntime(decision_loop=decision_loop, market_data_engine=streaming_engine)
    handles.paper_runtime = paper_runtime
    try:
        paper_runtime.start()
        status.paper_runtime_running = True
        status.paper_runtime_note = "running"
        registered = get_registered_live_handles()
        status.dashboard_registered = registered is not None and registered.paper_runner is paper_runtime.paper_trading_runner
        status.dashboard_note = "live handles registered" if status.dashboard_registered else "registration missing"
    except Exception as exc:  # noqa: BLE001
        status.paper_runtime_note = f"failed: {exc}"
        status.dashboard_note = "skipped: paper runtime failed to start"
        _LOGGER.warning("live_runtime_bootstrap.paper_runtime.failed", extra={"error": str(exc)})

    return handles


__all__ = [
    "LiveRuntimeHandles",
    "LiveRuntimeStatus",
    "bootstrap_live_runtime",
]
