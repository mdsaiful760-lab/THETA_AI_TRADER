"""Institutional market data acquisition and publishing engine for THETA AI TRADER.

This module orchestrates broker transport, tick buffering, adapter invocation, and
snapshot publication. It depends on :class:`broker.base_broker.BaseBrokerClient`
for transport and never imports broker SDKs directly.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from broker.base_broker import (
    BaseBrokerClient,
    BrokerClientError,
    Exchange,
    HistoricalRequest,
    InstrumentRequest,
    QuoteRequest,
)
from core.event_bus import EventBus
from core.event_topics import EventTopics
from market_data.market_data_adapter import (
    AdapterBuildRequest,
    AdapterPermission,
    MarketDataAdapter,
)
from market_data.market_snapshot import MarketSnapshot, OptionType, SnapshotSource

MARKET_DATA_ENGINE_VERSION: str = "1.0.0"
ENGINE_NAME: str = "market_data"
DEFAULT_PUBLISH_INTERVAL_SECONDS: float = 1.0
DEFAULT_INSTRUMENT_CACHE_TTL_SECONDS: float = 86400.0
DEFAULT_CONNECT_TIMEOUT_SECONDS: float = 15.0
DEFAULT_MAX_SUBSCRIPTIONS: int = 200
DEFAULT_SUBSCRIBE_BATCH_SIZE: int = 50
DEFAULT_MINIMUM_PUBLISH_COVERAGE_RATIO: float = 0.90
DEFAULT_VIX_QUOTE_KEY: str = "NSE:INDIA VIX"

ERROR_CONFIG_INVALID: str = "MARKET_DATA_ENGINE.CONFIG.INVALID"
ERROR_CONNECTION_TIMEOUT: str = "MARKET_DATA_ENGINE.CONNECTION.TIMEOUT"
ERROR_CONNECTION_FAILED: str = "MARKET_DATA_ENGINE.CONNECTION.FAILED"
ERROR_CONNECTION_DISCONNECTED: str = "MARKET_DATA_ENGINE.CONNECTION.DISCONNECTED"
ERROR_WEBSOCKET_ERROR: str = "MARKET_DATA_ENGINE.WEBSOCKET.ERROR"
ERROR_SUBSCRIBE_FAILED: str = "MARKET_DATA_ENGINE.SUBSCRIBE.FAILED"
ERROR_SUBSCRIBE_LIMIT_EXCEEDED: str = "MARKET_DATA_ENGINE.SUBSCRIBE.LIMIT_EXCEEDED"
ERROR_BUFFER_OVERFLOW: str = "MARKET_DATA_ENGINE.BUFFER.OVERFLOW"
ERROR_PUBLISH_SKIPPED_DEGRADED: str = "MARKET_DATA_ENGINE.PUBLISH.SKIPPED_DEGRADED"
ERROR_PUBLISH_SKIPPED_COVERAGE: str = "MARKET_DATA_ENGINE.PUBLISH.SKIPPED_COVERAGE"
ERROR_PUBLISH_ADAPTER_BLOCK: str = "MARKET_DATA_ENGINE.PUBLISH.ADAPTER_BLOCK"
ERROR_PUBLISH_ASSEMBLY_FAILED: str = "MARKET_DATA_ENGINE.PUBLISH.ASSEMBLY_FAILED"
ERROR_HEARTBEAT_STALE: str = "MARKET_DATA_ENGINE.HEARTBEAT.STALE"
ERROR_RECONNECT_EXHAUSTED: str = "MARKET_DATA_ENGINE.RECONNECT.EXHAUSTED"

_logger = logging.getLogger("market_data.market_data_engine")


class EngineRunState(str, Enum):
    """Primary engine lifecycle state."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


class ConnectionStatus(str, Enum):
    """Transport health overlay."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"


class PublishMode(str, Enum):
    """Controls fail-closed strictness for publishing."""

    LIVE = "live"
    ANALYSIS = "analysis"
    BACKTEST = "backtest"


class PublishOutcome(str, Enum):
    """Last publish attempt result."""

    PUBLISHED = "published"
    SKIPPED = "skipped"
    FAILED = "failed"


class SubscriptionState(str, Enum):
    """Per-instrument subscription tracking."""

    PENDING = "pending"
    ACTIVE = "active"
    FAILED = "failed"
    UNSUBSCRIBED = "unsubscribed"


class MarketDataEngineConfigurationError(Exception):
    """Raised when static engine configuration is invalid."""

    def __init__(self, message: str, *, code: str = ERROR_CONFIG_INVALID) -> None:
        super().__init__(message)
        self.code = code


class MarketDataEngineConnectionError(Exception):
    """Raised for unrecoverable connection failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str = ERROR_CONNECTION_FAILED,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.recoverable = recoverable


class MarketDataEnginePublishError(Exception):
    """Raised when publish is aborted due to engine policy."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EngineErrorRecord:
    """Structured engine error record."""

    code: str
    message: str
    field: str | None = None
    recoverable: bool = False


@dataclass(frozen=True)
class ReconnectPolicy:
    """Reconnect backoff policy."""

    max_attempts: int = 10
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    jitter_ratio: float = 0.2
    reset_attempts_after_seconds: float = 300.0


@dataclass(frozen=True)
class HeartbeatPolicy:
    """Heartbeat silence detection policy."""

    max_silence_seconds: float = 30.0
    check_interval_seconds: float = 5.0
    grace_after_subscribe_seconds: float = 10.0


@dataclass(frozen=True)
class UniverseConfig:
    """Instrument universe definition."""

    underlying: str
    exchange: str = "NFO"
    strikes_each_side: int = 10
    include_vix: bool = True
    spot_symbol: str = "NIFTY 50"
    spot_exchange: str = "NSE"
    spot_quote_key: str = "NSE:NIFTY 50"
    vix_quote_key: str = DEFAULT_VIX_QUOTE_KEY
    option_types: tuple[OptionType, ...] = (OptionType.CE, OptionType.PE)


@dataclass(frozen=True)
class MarketDataEngineConfig:
    """Frozen runtime configuration for the market data engine."""

    universe: UniverseConfig
    publish_interval_seconds: float = DEFAULT_PUBLISH_INTERVAL_SECONDS
    publish_mode: PublishMode = PublishMode.LIVE
    reconnect_policy: ReconnectPolicy = field(default_factory=ReconnectPolicy)
    heartbeat_policy: HeartbeatPolicy = field(default_factory=HeartbeatPolicy)
    instrument_cache_ttl_seconds: float = DEFAULT_INSTRUMENT_CACHE_TTL_SECONDS
    minimum_publish_coverage_ratio: float = DEFAULT_MINIMUM_PUBLISH_COVERAGE_RATIO
    universe_rebalance_strike_steps: int = 2
    max_buffer_entries: int = 256
    stale_entry_ttl_seconds: float = 300.0
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    max_subscriptions: int = DEFAULT_MAX_SUBSCRIPTIONS
    subscribe_batch_size: int = DEFAULT_SUBSCRIBE_BATCH_SIZE
    timezone: str = "Asia/Kolkata"


@dataclass(frozen=True)
class ConnectionInfo:
    """Immutable connection diagnostics snapshot."""

    status: ConnectionStatus
    since: datetime | None
    last_error: str | None = None
    reconnect_attempt: int = 0


@dataclass(frozen=True)
class BufferStats:
    """Tick buffer metrics."""

    instrument_count: int
    tick_count: int
    oldest_tick_age_seconds: float | None
    memory_estimate_bytes: int


@dataclass(frozen=True)
class SubscriptionRecord:
    """Subscription tracking record."""

    instrument_token: int
    quote_key: str
    state: SubscriptionState
    subscribed_at: datetime | None


@dataclass(frozen=True)
class PublishEvent:
    """Immutable publish outcome event."""

    outcome: PublishOutcome
    correlation_id: str
    as_of: datetime
    published_at: datetime
    duration_ms: float
    snapshot: MarketSnapshot | None = None
    adapter_permission: AdapterPermission | None = None
    reason_code: str = ""
    reason_message: str = ""


@dataclass(frozen=True)
class EngineHealth:
    """Health check snapshot."""

    healthy: bool
    connection_status: ConnectionStatus
    last_publish_at: datetime | None
    last_successful_publish_at: datetime | None
    issues: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalCandleRequest:
    """Historical candle fetch request."""

    instrument_key: str
    interval: str
    from_ts: datetime
    to_ts: datetime
    continuous: bool = False
    correlation_id: str | None = None


@dataclass(frozen=True)
class HistoricalCandleResult:
    """Raw historical candle fetch result."""

    instrument_key: str
    candles: tuple[Mapping[str, object], ...]
    correlation_id: str | None = None


@runtime_checkable
class MarketDataSafetyProtocol(Protocol):
    """Optional publish-time session safety collaborator."""

    def is_market_open(self, current_time: datetime | None = None) -> bool:
        """Return whether the regular session is open."""


@runtime_checkable
class EngineMetricsRecorder(Protocol):
    """Optional metrics hook surface."""

    def set_gauge(self, name: str, value: float, *, labels: Mapping[str, str] | None = None) -> None:
        """Set a gauge metric."""

    def increment_counter(
        self,
        name: str,
        *,
        value: float = 1.0,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Increment a counter metric."""

    def observe_histogram(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        """Record a histogram observation."""


class _NoOpMetricsRecorder:
    """Default metrics recorder."""

    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        return None

    def increment_counter(
        self,
        name: str,
        *,
        value: float = 1.0,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        return None

    def observe_histogram(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        return None


@dataclass(frozen=True)
class TickEntry:
    """Immutable latest tick state for one instrument."""

    instrument_token: int
    quote_key: str
    last_price: float
    timestamp: datetime
    received_at: datetime
    volume: int | None = None
    oi: int | None = None
    raw_tick: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


class TickBuffer:
    """Thread-safe latest-tick store keyed by instrument token."""

    def __init__(self, *, max_entries: int) -> None:
        self._max_entries = max_entries
        self._lock = threading.RLock()
        self._entries: dict[int, TickEntry] = {}
        self._quote_key_index: dict[str, int] = {}
        self._tick_count = 0

    def upsert(self, tick: Mapping[str, object], *, quote_key: str | None = None) -> None:
        """Insert or replace the latest tick for an instrument token."""
        token_raw = tick.get("instrument_token")
        if token_raw is None:
            return
        token = int(token_raw)
        last_price = _safe_float(tick.get("last_price"))
        if last_price is None or last_price <= 0:
            return
        resolved_quote_key = quote_key or str(tick.get("quote_key", f"TOKEN:{token}"))
        timestamp = _coerce_timestamp(tick.get("timestamp")) or _utc_now()
        received_at = _utc_now()
        entry = TickEntry(
            instrument_token=token,
            quote_key=resolved_quote_key,
            last_price=last_price,
            timestamp=timestamp,
            received_at=received_at,
            volume=_safe_int(tick.get("volume")),
            oi=_safe_int(tick.get("oi") or tick.get("open_interest")),
            raw_tick=MappingProxyType(dict(tick)),
        )
        with self._lock:
            if token not in self._entries and len(self._entries) >= self._max_entries:
                _logger.warning(
                    "market_data_engine.buffer.overflow",
                    extra={"code": ERROR_BUFFER_OVERFLOW, "token": token},
                )
                return
            self._entries[token] = entry
            self._quote_key_index[resolved_quote_key] = token
            self._tick_count += 1

    def get(self, instrument_token: int) -> TickEntry | None:
        """Return a tick entry copy for a token."""
        with self._lock:
            return self._entries.get(instrument_token)

    def snapshot_tokens(self, tokens: Sequence[int]) -> tuple[TickEntry, ...]:
        """Return tick entries for the requested tokens."""
        with self._lock:
            return tuple(
                entry
                for token in tokens
                if (entry := self._entries.get(token)) is not None
            )

    def prune(self, keep_tokens: frozenset[int]) -> int:
        """Remove entries not in the keep set."""
        removed = 0
        with self._lock:
            stale = [token for token in self._entries if token not in keep_tokens]
            for token in stale:
                entry = self._entries.pop(token, None)
                if entry is not None:
                    self._quote_key_index.pop(entry.quote_key, None)
                    removed += 1
        return removed

    def stats(self) -> BufferStats:
        """Return buffer statistics."""
        with self._lock:
            now = _utc_now()
            ages = [
                (now - entry.received_at).total_seconds()
                for entry in self._entries.values()
            ]
            oldest = max(ages) if ages else None
            memory_estimate = len(self._entries) * 512
            return BufferStats(
                instrument_count=len(self._entries),
                tick_count=self._tick_count,
                oldest_tick_age_seconds=oldest,
                memory_estimate_bytes=memory_estimate,
            )

    def quote_key_for_token(self, instrument_token: int) -> str | None:
        """Return quote key for a token when known."""
        with self._lock:
            entry = self._entries.get(instrument_token)
            return entry.quote_key if entry is not None else None


class SnapshotPublisher:
    """Thread-safe snapshot fan-out with EventBus integration."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._lock = threading.RLock()
        self._subscribers: list[Callable[[PublishEvent], None]] = []

    def add_subscriber(self, callback: Callable[[PublishEvent], None]) -> None:
        """Register a snapshot subscriber callback."""
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def remove_subscriber(self, callback: Callable[[PublishEvent], None]) -> None:
        """Unregister a snapshot subscriber callback."""
        with self._lock:
            self._subscribers = [item for item in self._subscribers if item is not callback]

    def emit(self, event: PublishEvent) -> None:
        """Publish an event to subscribers and the event bus."""
        topic = _topic_for_outcome(event.outcome)
        try:
            self._event_bus.publish(
                topic,
                event,
                correlation_id=event.correlation_id,
                producer=ENGINE_NAME,
                occurred_at=event.as_of,
                producer_version=MARKET_DATA_ENGINE_VERSION,
                payload_type="PublishEvent",
            )
        except Exception:  # noqa: BLE001 - bus isolation
            _logger.exception("market_data_engine.event_bus.publish_failed")

        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:  # noqa: BLE001 - subscriber isolation
                _logger.exception("market_data_engine.subscriber.failed")


class MarketDataEngine:
    """Production market data acquisition and publishing engine.

    The engine is infrastructure — not a :class:`core.base_engine.BaseEngine`
    analytical subclass. It coordinates broker transport, buffering, adapter
    invocation, and event publication.

    Args:
        config: Immutable engine configuration.
        broker_client: Injected authenticated broker transport client.
        adapter: Injected market data normalizer.
        event_bus: Injected event bus for snapshot publication.
        safety: Optional market session safety collaborator.
        metrics: Optional metrics recorder hooks.
        monotonic_clock: Optional monotonic clock for tests.
        time_fn: Optional wall-clock provider for tests.
    """

    def __init__(
        self,
        config: MarketDataEngineConfig,
        broker_client: BaseBrokerClient,
        adapter: MarketDataAdapter,
        event_bus: EventBus,
        *,
        safety: MarketDataSafetyProtocol | None = None,
        metrics: EngineMetricsRecorder | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        time_fn: Callable[[], datetime] | None = None,
    ) -> None:
        _validate_engine_config(config)
        self._config = config
        self._broker = broker_client
        self._adapter = adapter
        self._publisher = SnapshotPublisher(event_bus)
        self._safety = safety
        self._metrics = metrics or _NoOpMetricsRecorder()
        self._monotonic = monotonic_clock or time.monotonic
        self._time_fn = time_fn or _utc_now

        self._lock = threading.RLock()
        self._state = EngineRunState.STOPPED
        self._connection_status = ConnectionStatus.DISCONNECTED
        self._connected_since: datetime | None = None
        self._last_error: str | None = None
        self._reconnect_attempt = 0

        self._buffer = TickBuffer(max_entries=config.max_buffer_entries)
        self._instrument_cache: dict[str, tuple[Mapping[str, object], ...]] = {}
        self._instrument_cache_loaded_at: dict[str, datetime] = {}
        self._token_to_quote_key: dict[int, str] = {}
        self._desired_tokens: frozenset[int] = frozenset()
        self._active_tokens: dict[int, SubscriptionState] = {}
        self._subscription_records: dict[int, SubscriptionRecord] = {}

        self._shutdown = threading.Event()
        self._publish_wake = threading.Condition()
        self._publish_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._reconnect_thread: threading.Thread | None = None

        self._last_activity_monotonic = self._monotonic()
        self._last_publish_at: datetime | None = None
        self._last_successful_publish_at: datetime | None = None
        self._last_publish_outcome: PublishOutcome | None = None

    @property
    def engine_name(self) -> str:
        """Return stable engine identifier."""
        return ENGINE_NAME

    @property
    def engine_version(self) -> str:
        """Return engine semantic version."""
        return MARKET_DATA_ENGINE_VERSION

    def start(self) -> None:
        """Connect, subscribe, and start background workers."""
        with self._lock:
            if self._state is EngineRunState.RUNNING:
                _logger.warning("market_data_engine.start.noop_already_running")
                return
            if self._state is EngineRunState.STARTING:
                return
            self._state = EngineRunState.STARTING
            self._shutdown.clear()

        _logger.info("market_data_engine.start", extra={"engine_name": ENGINE_NAME})
        try:
            self._connect_and_validate()
            self._ensure_instrument_caches(force=True)
            self._resolve_and_subscribe_universe()
            self._register_broker_callbacks()
            self._start_background_workers()
            with self._lock:
                self._state = EngineRunState.RUNNING
            self._metrics.set_gauge(
                "market_data_connection_status",
                float(_status_code(self._connection_status)),
            )
            _logger.info("market_data_engine.connected")
        except MarketDataEngineConnectionError:
            with self._lock:
                self._state = EngineRunState.STOPPED
                self._connection_status = ConnectionStatus.DISCONNECTED
            raise
        except MarketDataEngineConfigurationError:
            with self._lock:
                self._state = EngineRunState.STOPPED
                self._connection_status = ConnectionStatus.DISCONNECTED
            raise
        except Exception as exc:
            with self._lock:
                self._state = EngineRunState.STOPPED
                self._connection_status = ConnectionStatus.DISCONNECTED
            raise MarketDataEngineConnectionError(
                str(exc),
                code=ERROR_CONNECTION_FAILED,
            ) from exc

    def stop(self) -> None:
        """Stop workers, unsubscribe, and disconnect."""
        with self._lock:
            if self._state is EngineRunState.STOPPED:
                return
            self._state = EngineRunState.STOPPING

        self._shutdown.set()
        with self._publish_wake:
            self._publish_wake.notify_all()

        for thread in (self._publish_thread, self._heartbeat_thread, self._reconnect_thread):
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=5.0)

        try:
            if self._desired_tokens:
                self._broker.unsubscribe(tuple(self._desired_tokens))
        except BrokerClientError:
            _logger.warning("market_data_engine.unsubscribe.failed")

        try:
            self._broker.disconnect()
        except BrokerClientError:
            _logger.warning("market_data_engine.disconnect.failed")

        with self._lock:
            self._active_tokens.clear()
            self._subscription_records.clear()
            self._connection_status = ConnectionStatus.DISCONNECTED
            self._state = EngineRunState.STOPPED
            self._publish_thread = None
            self._heartbeat_thread = None
            self._reconnect_thread = None

        _logger.info("market_data_engine.disconnected")

    def publish_snapshot(
        self,
        *,
        correlation_id: str | None = None,
        as_of: datetime | None = None,
        force: bool = False,
    ) -> PublishEvent:
        """Publish a one-shot market snapshot."""
        resolved_correlation = correlation_id or str(uuid.uuid4())
        resolved_as_of = as_of or self._now_in_config_tz()
        return self._publish_snapshot_internal(
            correlation_id=resolved_correlation,
            as_of=resolved_as_of,
            force=force,
        )

    def get_connection_info(self) -> ConnectionInfo:
        """Return current connection diagnostics."""
        with self._lock:
            return ConnectionInfo(
                status=self._connection_status,
                since=self._connected_since,
                last_error=self._last_error,
                reconnect_attempt=self._reconnect_attempt,
            )

    def get_buffer_stats(self) -> BufferStats:
        """Return tick buffer statistics."""
        return self._buffer.stats()

    def get_subscriptions(self) -> tuple[SubscriptionRecord, ...]:
        """Return subscription snapshot records."""
        with self._lock:
            return tuple(self._subscription_records.values())

    def refresh_instruments(self) -> int:
        """Force instrument master reload."""
        rows = self._load_instruments(force=True)
        return len(rows)

    def fetch_historical_candles(
        self,
        request: HistoricalCandleRequest,
    ) -> HistoricalCandleResult:
        """Fetch raw historical candles without normalization."""
        broker_request = HistoricalRequest(
            instrument_key=request.instrument_key,
            interval=request.interval,
            from_ts=request.from_ts,
            to_ts=request.to_ts,
            continuous=request.continuous,
        )
        candles = self._broker.fetch_historical(broker_request)
        return HistoricalCandleResult(
            instrument_key=request.instrument_key,
            candles=candles,
            correlation_id=request.correlation_id,
        )

    def add_subscriber(self, callback: Callable[[PublishEvent], None]) -> None:
        """Register a snapshot subscriber."""
        self._publisher.add_subscriber(callback)

    def remove_subscriber(self, callback: Callable[[PublishEvent], None]) -> None:
        """Remove a snapshot subscriber."""
        self._publisher.remove_subscriber(callback)

    def health_check(self) -> EngineHealth:
        """Return engine health snapshot."""
        with self._lock:
            issues: list[str] = []
            if self._state is not EngineRunState.RUNNING:
                issues.append("engine_not_running")
            if self._connection_status in {
                ConnectionStatus.DISCONNECTED,
                ConnectionStatus.DEGRADED,
                ConnectionStatus.RECONNECTING,
            }:
                issues.append(f"connection_{self._connection_status.value}")
            if self._last_successful_publish_at is None and self._state is EngineRunState.RUNNING:
                issues.append("no_successful_publish")
            healthy = not issues
            return EngineHealth(
                healthy=healthy,
                connection_status=self._connection_status,
                last_publish_at=self._last_publish_at,
                last_successful_publish_at=self._last_successful_publish_at,
                issues=tuple(issues),
            )

    def _connect_and_validate(self) -> None:
        with self._lock:
            self._connection_status = ConnectionStatus.CONNECTING

        deadline = self._monotonic() + self._config.connect_timeout_seconds
        if not self._broker.is_connected():
            self._broker.connect()

        while self._monotonic() < deadline:
            probe = self._broker.fetch_ltp(
                QuoteRequest(instrument_keys=(self._config.universe.spot_quote_key,))
            )
            spot_payload = probe.get(self._config.universe.spot_quote_key)
            spot_price = _safe_float(
                spot_payload.get("last_price") if isinstance(spot_payload, Mapping) else None
            )
            if spot_price is not None and spot_price > 0:
                with self._lock:
                    self._connection_status = ConnectionStatus.CONNECTED
                    self._connected_since = _utc_now()
                    self._record_activity()
                return
            time.sleep(0.05)

        with self._lock:
            self._last_error = "spot probe failed"
        raise MarketDataEngineConnectionError(
            "REST spot probe failed during connect",
            code=ERROR_CONNECTION_TIMEOUT,
        )

    def _load_instruments(self, *, force: bool) -> tuple[Mapping[str, object], ...]:
        """Load and cache derivative instruments for the configured exchange."""
        return self._load_exchange_instruments(self._config.universe.exchange, force=force)

    def _load_exchange_instruments(
        self,
        exchange_code: str,
        *,
        force: bool,
    ) -> tuple[Mapping[str, object], ...]:
        now = _utc_now()
        cache_key = exchange_code.upper()
        loaded_at = self._instrument_cache_loaded_at.get(cache_key)
        if (
            not force
            and loaded_at is not None
            and (now - loaded_at).total_seconds() < self._config.instrument_cache_ttl_seconds
            and cache_key in self._instrument_cache
        ):
            return self._instrument_cache[cache_key]

        exchange = Exchange(exchange_code.lower())
        rows = self._broker.fetch_instruments(InstrumentRequest(exchange=exchange))
        self._instrument_cache[cache_key] = rows
        self._instrument_cache_loaded_at[cache_key] = now
        self._index_instruments(rows)
        return rows

    def _ensure_instrument_caches(self, *, force: bool) -> None:
        self._load_exchange_instruments(self._config.universe.exchange, force=force)
        self._load_exchange_instruments(self._config.universe.spot_exchange, force=force)

    def _index_instruments(self, rows: Sequence[Mapping[str, object]]) -> None:
        token_map: dict[int, str] = {}
        for row in rows:
            token_raw = row.get("instrument_token")
            exchange = row.get("exchange")
            symbol = row.get("tradingsymbol")
            if token_raw is None or exchange is None or symbol is None:
                continue
            quote_key = f"{exchange}:{symbol}"
            token_map[int(token_raw)] = quote_key
        with self._lock:
            self._token_to_quote_key.update(token_map)

    def _resolve_and_subscribe_universe(self) -> None:
        nfo_rows = self._load_instruments(force=False)
        spot_price = self._fetch_spot_price()
        tokens = self._compute_desired_tokens(nfo_rows, spot_price)
        if len(tokens) > self._config.max_subscriptions:
            raise MarketDataEngineConfigurationError(
                "subscription limit exceeded",
                code=ERROR_SUBSCRIBE_LIMIT_EXCEEDED,
            )
        with self._lock:
            self._desired_tokens = frozenset(tokens)
        self._subscribe_tokens(tokens)

    def _compute_desired_tokens(
        self,
        nfo_rows: Sequence[Mapping[str, object]],
        spot_price: float,
    ) -> tuple[int, ...]:
        universe = self._config.universe
        expiry = self._adapter.get_nearest_expiry(
            nfo_rows,
            universe.underlying,
            exchange=universe.exchange,
        )
        if expiry is None:
            raise MarketDataEngineConnectionError(
                "no valid expiry for universe",
                code=ERROR_PUBLISH_ASSEMBLY_FAILED,
            )

        strikes = self._adapter.get_available_strikes(
            nfo_rows,
            universe.underlying,
            expiry,
            exchange=universe.exchange,
        )
        nearby = self._adapter.get_nearby_strikes(
            strikes,
            spot_price,
            universe.strikes_each_side,
        )

        tokens: list[int] = []
        quote_keys: dict[int, str] = {}
        for raw in nfo_rows:
            normalized = self._adapter.normalize_instrument(raw)
            if not normalized.valid or normalized.value is None:
                continue
            instrument = normalized.value
            if instrument.underlying != universe.underlying.upper():
                continue
            if instrument.expiry != expiry:
                continue
            if instrument.strike not in nearby:
                continue
            if instrument.option_type not in universe.option_types:
                continue
            tokens.append(instrument.instrument_token)
            quote_keys[instrument.instrument_token] = instrument.quote_key

        spot_token = self._find_token_by_quote_key(
            self._instrument_cache.get(universe.spot_exchange.upper(), ()),
            universe.spot_quote_key,
        )
        if spot_token is not None:
            tokens.append(spot_token)
            quote_keys[spot_token] = universe.spot_quote_key

        if universe.include_vix:
            vix_token = self._find_token_by_quote_key(
                self._instrument_cache.get(universe.spot_exchange.upper(), ()),
                universe.vix_quote_key,
            )
            if vix_token is not None:
                tokens.append(vix_token)
                quote_keys[vix_token] = universe.vix_quote_key

        with self._lock:
            self._token_to_quote_key.update(quote_keys)
        return tuple(sorted(set(tokens)))

    def _find_token_by_quote_key(
        self,
        rows: Sequence[Mapping[str, object]],
        quote_key: str,
    ) -> int | None:
        for row in rows:
            exchange = row.get("exchange")
            symbol = row.get("tradingsymbol")
            token = row.get("instrument_token")
            if exchange is None or symbol is None or token is None:
                continue
            if f"{exchange}:{symbol}" == quote_key:
                return int(token)
        return None

    def _subscribe_tokens(self, tokens: Sequence[int]) -> None:
        batch_size = self._config.subscribe_batch_size
        now = _utc_now()
        for start in range(0, len(tokens), batch_size):
            batch = tuple(tokens[start : start + batch_size])
            try:
                self._broker.subscribe(batch)
            except BrokerClientError as exc:
                _logger.error(
                    "market_data_engine.subscribe.failed",
                    extra={"code": ERROR_SUBSCRIBE_FAILED},
                )
                raise MarketDataEngineConnectionError(
                    str(exc),
                    code=ERROR_SUBSCRIBE_FAILED,
                ) from exc
            with self._lock:
                for token in batch:
                    quote_key = self._token_to_quote_key.get(token, f"TOKEN:{token}")
                    self._active_tokens[token] = SubscriptionState.PENDING
                    self._subscription_records[token] = SubscriptionRecord(
                        instrument_token=token,
                        quote_key=quote_key,
                        state=SubscriptionState.PENDING,
                        subscribed_at=now,
                    )
        _logger.info(
            "market_data_engine.subscribe",
            extra={"count": len(tokens)},
        )
        self._metrics.set_gauge("market_data_subscriptions_active", float(len(tokens)))

    def _register_broker_callbacks(self) -> None:
        self._broker.set_tick_handler(self._on_tick)
        self._broker.set_error_handler(self._on_broker_error)
        self._broker.set_connection_handler(self._on_broker_connection)

    def _start_background_workers(self) -> None:
        self._publish_thread = threading.Thread(
            target=self._publish_worker,
            name="market-data-publish",
            daemon=True,
        )
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_worker,
            name="market-data-heartbeat",
            daemon=True,
        )
        self._publish_thread.start()
        self._heartbeat_thread.start()

    def _publish_worker(self) -> None:
        while not self._shutdown.is_set():
            with self._publish_wake:
                self._publish_wake.wait(timeout=self._config.publish_interval_seconds)
            if self._shutdown.is_set():
                break
            with self._lock:
                running = self._state is EngineRunState.RUNNING
            if not running:
                continue
            try:
                self.publish_snapshot()
            except Exception:  # noqa: BLE001 - worker isolation
                _logger.exception("market_data_engine.publish.worker_failed")

    def _heartbeat_worker(self) -> None:
        policy = self._config.heartbeat_policy
        while not self._shutdown.is_set():
            if self._shutdown.wait(policy.check_interval_seconds):
                break
            silence = self._monotonic() - self._last_activity_monotonic
            self._metrics.set_gauge("market_data_heartbeat_lag_seconds", silence)
            if silence > policy.max_silence_seconds:
                _logger.warning(
                    "market_data_engine.heartbeat.stale",
                    extra={"code": ERROR_HEARTBEAT_STALE, "silence_seconds": silence},
                )
                with self._lock:
                    self._connection_status = ConnectionStatus.DEGRADED
                    self._last_error = ERROR_HEARTBEAT_STALE
                _logger.warning("market_data_engine.degraded")
                self._trigger_reconnect()

    def _trigger_reconnect(self) -> None:
        with self._lock:
            if self._reconnect_thread is not None and self._reconnect_thread.is_alive():
                return
            self._connection_status = ConnectionStatus.RECONNECTING
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_worker,
                name="market-data-reconnect",
                daemon=True,
            )
            self._reconnect_thread.start()

    def _reconnect_worker(self) -> None:
        policy = self._config.reconnect_policy
        for attempt in range(policy.max_attempts):
            if self._shutdown.is_set():
                return
            delay = min(
                policy.initial_delay_seconds * (2**attempt),
                policy.max_delay_seconds,
            )
            delay *= 1.0 + random.uniform(-policy.jitter_ratio, policy.jitter_ratio)
            _logger.info(
                "market_data_engine.reconnect.attempt",
                extra={"attempt": attempt + 1},
            )
            self._metrics.increment_counter("market_data_reconnect_attempts_total")
            time.sleep(max(delay, 0.0))
            try:
                if not self._broker.is_connected():
                    self._broker.connect()
                self._subscribe_tokens(tuple(self._desired_tokens))
                self._record_activity()
                with self._lock:
                    self._connection_status = ConnectionStatus.CONNECTED
                    self._reconnect_attempt = 0
                    self._last_error = None
                _logger.info("market_data_engine.connected")
                return
            except BrokerClientError as exc:
                with self._lock:
                    self._reconnect_attempt = attempt + 1
                    self._last_error = str(exc)

        with self._lock:
            self._connection_status = ConnectionStatus.DISCONNECTED
            self._state = EngineRunState.STOPPED
            self._last_error = ERROR_RECONNECT_EXHAUSTED
        _logger.error(
            "market_data_engine.reconnect.exhausted",
            extra={"code": ERROR_RECONNECT_EXHAUSTED},
        )

    def _on_tick(self, tick: Mapping[str, object]) -> None:
        token_raw = tick.get("instrument_token")
        if token_raw is None:
            return
        token = int(token_raw)
        quote_key = self._token_to_quote_key.get(token)
        self._buffer.upsert(tick, quote_key=quote_key)
        self._record_activity()
        self._metrics.increment_counter("market_data_ticks_received_total")
        with self._lock:
            if token in self._active_tokens:
                self._active_tokens[token] = SubscriptionState.ACTIVE
                existing = self._subscription_records.get(token)
                if existing is not None:
                    self._subscription_records[token] = SubscriptionRecord(
                        instrument_token=token,
                        quote_key=existing.quote_key,
                        state=SubscriptionState.ACTIVE,
                        subscribed_at=existing.subscribed_at,
                    )

    def _on_broker_error(self, error: BrokerClientError) -> None:
        _logger.error(
            "market_data_engine.ws.error",
            extra={"code": ERROR_WEBSOCKET_ERROR},
        )
        with self._lock:
            self._connection_status = ConnectionStatus.DEGRADED
            self._last_error = error.message
        self._trigger_reconnect()

    def _on_broker_connection(self, _info: object) -> None:
        self._record_activity()

    def _record_activity(self) -> None:
        self._last_activity_monotonic = self._monotonic()

    def _publish_snapshot_internal(
        self,
        *,
        correlation_id: str,
        as_of: datetime,
        force: bool,
    ) -> PublishEvent:
        start = time.perf_counter()
        published_at = _utc_now()

        skip_reason_code = ""
        skip_reason_message = ""
        if not force:
            skip_reason_code, skip_reason_message = self._evaluate_publish_gating(as_of)
            if skip_reason_code:
                event = PublishEvent(
                    outcome=PublishOutcome.SKIPPED,
                    correlation_id=correlation_id,
                    as_of=as_of,
                    published_at=published_at,
                    duration_ms=(time.perf_counter() - start) * 1000.0,
                    reason_code=skip_reason_code,
                    reason_message=skip_reason_message,
                )
                self._finalize_publish(event)
                return event

        try:
            adapter_result, kite_quotes = self._assemble_and_build(
                correlation_id=correlation_id,
                as_of=as_of,
            )
        except Exception as exc:  # noqa: BLE001 - assembly boundary
            event = PublishEvent(
                outcome=PublishOutcome.FAILED,
                correlation_id=correlation_id,
                as_of=as_of,
                published_at=published_at,
                duration_ms=(time.perf_counter() - start) * 1000.0,
                reason_code=ERROR_PUBLISH_ASSEMBLY_FAILED,
                reason_message=str(exc),
            )
            self._finalize_publish(event)
            return event

        if adapter_result.permission is AdapterPermission.BLOCK:
            event = PublishEvent(
                outcome=PublishOutcome.FAILED,
                correlation_id=correlation_id,
                as_of=as_of,
                published_at=published_at,
                duration_ms=(time.perf_counter() - start) * 1000.0,
                adapter_permission=adapter_result.permission,
                reason_code=ERROR_PUBLISH_ADAPTER_BLOCK,
                reason_message=adapter_result.reason,
            )
            self._finalize_publish(event)
            return event

        event = PublishEvent(
            outcome=PublishOutcome.PUBLISHED,
            correlation_id=correlation_id,
            as_of=as_of,
            published_at=published_at,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            snapshot=adapter_result.snapshot,
            adapter_permission=adapter_result.permission,
        )
        self._finalize_publish(event)
        return event

    def _evaluate_publish_gating(self, as_of: datetime) -> tuple[str, str]:
        with self._lock:
            status = self._connection_status
            mode = self._config.publish_mode
        if status is ConnectionStatus.DISCONNECTED:
            return ERROR_CONNECTION_DISCONNECTED, "broker disconnected"
        if (
            mode is PublishMode.LIVE
            and status is ConnectionStatus.DEGRADED
            and not self._rest_fallback_complete()
        ):
            return ERROR_PUBLISH_SKIPPED_DEGRADED, "connection degraded"
        if mode is PublishMode.LIVE and self._safety is not None:
            if not self._safety.is_market_open(as_of):
                return ERROR_PUBLISH_SKIPPED_COVERAGE, "market closed"
        coverage = self._tick_coverage_ratio()
        if (
            mode is PublishMode.LIVE
            and coverage < self._config.minimum_publish_coverage_ratio
        ):
            return (
                ERROR_PUBLISH_SKIPPED_COVERAGE,
                f"coverage {coverage:.2f} below minimum",
            )
        return "", ""

    def _tick_coverage_ratio(self) -> float:
        if not self._desired_tokens:
            return 0.0
        covered = len(self._buffer.snapshot_tokens(tuple(self._desired_tokens)))
        return covered / len(self._desired_tokens)

    def _rest_fallback_complete(self) -> bool:
        missing_keys = self._missing_quote_keys()
        if not missing_keys:
            return True
        try:
            quotes = self._broker.fetch_quotes(QuoteRequest(instrument_keys=tuple(missing_keys)))
        except BrokerClientError:
            return False
        self._metrics.increment_counter("market_data_rest_fallback_total")
        return len(quotes) >= len(missing_keys)

    def _assemble_and_build(
        self,
        *,
        correlation_id: str,
        as_of: datetime,
    ) -> tuple[object, Mapping[str, Mapping[str, object]]]:
        nfo_rows = self._load_instruments(force=False)
        kite_quotes = self._assemble_quotes()
        spot_quote = kite_quotes.get(
            self._config.universe.spot_quote_key,
            {"last_price": self._fetch_spot_price()},
        )
        vix_quote = None
        if self._config.universe.include_vix:
            vix_quote = kite_quotes.get(self._config.universe.vix_quote_key)

        request = AdapterBuildRequest(
            underlying=self._config.universe.underlying,
            as_of=as_of,
            correlation_id=correlation_id,
            exchange=self._config.universe.exchange,
            strikes_each_side=self._config.universe.strikes_each_side,
            option_types=self._config.universe.option_types,
            captured_at=self._time_fn(),
            source=SnapshotSource.LIVE,
        )
        result = self._adapter.build_market_snapshot_from_kite(
            kite_instruments=nfo_rows,
            kite_quotes=kite_quotes,
            kite_spot_quote=spot_quote,
            kite_vix_quote=vix_quote,
            request=request,
            spot_symbol=self._config.universe.spot_symbol,
            spot_exchange=self._config.universe.spot_exchange,
            spot_quote_key=self._config.universe.spot_quote_key,
        )
        return result, kite_quotes

    def _assemble_quotes(self) -> dict[str, Mapping[str, object]]:
        quote_keys = tuple(
            key
            for token in self._desired_tokens
            if (key := self._token_to_quote_key.get(token)) is not None
        )
        rest_quotes = self._broker.fetch_quotes(QuoteRequest(instrument_keys=quote_keys))
        self._metrics.increment_counter("market_data_rest_fallback_total")
        quotes: dict[str, Mapping[str, object]] = {
            key: MappingProxyType(dict(payload)) for key, payload in rest_quotes.items()
        }

        for token in self._desired_tokens:
            quote_key = self._token_to_quote_key.get(token)
            entry = self._buffer.get(token)
            if quote_key is None or entry is None or quote_key not in quotes:
                continue
            merged = dict(quotes[quote_key])
            merged["last_price"] = entry.last_price
            merged["timestamp"] = entry.timestamp
            if entry.volume is not None:
                merged["volume"] = entry.volume
            if entry.oi is not None:
                merged["oi"] = entry.oi
            quotes[quote_key] = MappingProxyType(merged)
        return quotes

    def _missing_quote_keys(
        self,
        existing: Mapping[str, Mapping[str, object]] | None = None,
    ) -> tuple[str, ...]:
        existing = existing or {}
        missing: list[str] = []
        for token in self._desired_tokens:
            quote_key = self._token_to_quote_key.get(token)
            if quote_key is None:
                continue
            if quote_key not in existing:
                missing.append(quote_key)
        return tuple(missing)

    def _fetch_spot_price(self) -> float:
        probe = self._broker.fetch_ltp(
            QuoteRequest(instrument_keys=(self._config.universe.spot_quote_key,))
        )
        payload = probe.get(self._config.universe.spot_quote_key, {})
        price = _safe_float(payload.get("last_price") if isinstance(payload, Mapping) else None)
        if price is None or price <= 0:
            raise MarketDataEnginePublishError(
                "invalid spot price",
                code=ERROR_PUBLISH_ASSEMBLY_FAILED,
            )
        return price

    def _resolve_instrument_token(self, instrument_key: str) -> int:
        token = self._find_token_by_quote_key(
            self._instrument_cache.get(self._config.universe.exchange.upper(), ()),
            instrument_key,
        )
        if token is None:
            token = self._find_token_by_quote_key(
                self._instrument_cache.get(self._config.universe.spot_exchange.upper(), ()),
                instrument_key,
            )
        if token is None:
            raise MarketDataEnginePublishError(
                f"instrument token not found for {instrument_key}",
                code=ERROR_PUBLISH_ASSEMBLY_FAILED,
            )
        return token

    def _finalize_publish(self, event: PublishEvent) -> None:
        with self._lock:
            self._last_publish_at = event.published_at
            self._last_publish_outcome = event.outcome
            if event.outcome is PublishOutcome.PUBLISHED:
                self._last_successful_publish_at = event.published_at

        self._metrics.increment_counter(
            "market_data_publish_total",
            labels={"outcome": event.outcome.value},
        )
        self._metrics.observe_histogram(
            "market_data_publish_duration_seconds",
            event.duration_ms / 1000.0,
        )
        self._metrics.set_gauge(
            "market_data_buffer_entries",
            float(self._buffer.stats().instrument_count),
        )

        if event.outcome is PublishOutcome.PUBLISHED:
            _logger.info(
                "market_data_engine.publish.success",
                extra={
                    "correlation_id": event.correlation_id,
                    "permission": event.adapter_permission.value if event.adapter_permission else "",
                },
            )
        elif event.outcome is PublishOutcome.SKIPPED:
            _logger.info(
                "market_data_engine.publish.skipped",
                extra={"correlation_id": event.correlation_id, "code": event.reason_code},
            )
        else:
            _logger.warning(
                "market_data_engine.publish.failed",
                extra={"correlation_id": event.correlation_id, "code": event.reason_code},
            )

        self._publisher.emit(event)

    def _now_in_config_tz(self) -> datetime:
        return self._time_fn().astimezone(ZoneInfo(self._config.timezone))


def _validate_engine_config(config: MarketDataEngineConfig) -> None:
    if config.publish_interval_seconds <= 0:
        raise MarketDataEngineConfigurationError("publish_interval_seconds must be positive")
    if not (0.0 < config.minimum_publish_coverage_ratio <= 1.0):
        raise MarketDataEngineConfigurationError("minimum_publish_coverage_ratio must be in (0, 1]")
    if config.max_buffer_entries <= 0:
        raise MarketDataEngineConfigurationError("max_buffer_entries must be positive")
    if not config.universe.underlying.strip():
        raise MarketDataEngineConfigurationError("universe.underlying is required")


def _tick_entry_to_quote(entry: TickEntry) -> Mapping[str, object]:
    payload: dict[str, object] = {
        "instrument_token": entry.instrument_token,
        "last_price": entry.last_price,
        "timestamp": entry.timestamp,
    }
    if entry.volume is not None:
        payload["volume"] = entry.volume
    if entry.oi is not None:
        payload["oi"] = entry.oi
    return MappingProxyType(payload)


def _topic_for_outcome(outcome: PublishOutcome) -> str:
    if outcome is PublishOutcome.PUBLISHED:
        return EventTopics.MARKET_SNAPSHOT_PUBLISHED
    if outcome is PublishOutcome.SKIPPED:
        return EventTopics.MARKET_SNAPSHOT_SKIPPED
    return EventTopics.MARKET_SNAPSHOT_FAILED


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        result = float(value)
        if result != result:  # NaN
            return None
        return result
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _status_code(status: ConnectionStatus) -> int:
    mapping = {
        ConnectionStatus.DISCONNECTED: 0,
        ConnectionStatus.CONNECTING: 1,
        ConnectionStatus.CONNECTED: 2,
        ConnectionStatus.DEGRADED: 3,
        ConnectionStatus.RECONNECTING: 4,
    }
    return mapping[status]


__all__ = [
    "DEFAULT_INSTRUMENT_CACHE_TTL_SECONDS",
    "DEFAULT_PUBLISH_INTERVAL_SECONDS",
    "ENGINE_NAME",
    "MARKET_DATA_ENGINE_VERSION",
    "BufferStats",
    "ConnectionInfo",
    "ConnectionStatus",
    "EngineErrorRecord",
    "EngineHealth",
    "HeartbeatPolicy",
    "HistoricalCandleRequest",
    "HistoricalCandleResult",
    "MarketDataEngine",
    "MarketDataEngineConfig",
    "MarketDataEngineConfigurationError",
    "MarketDataEngineConnectionError",
    "MarketDataEnginePublishError",
    "PublishEvent",
    "PublishMode",
    "PublishOutcome",
    "ReconnectPolicy",
    "SnapshotPublisher",
    "SubscriptionRecord",
    "SubscriptionState",
    "UniverseConfig",
]
