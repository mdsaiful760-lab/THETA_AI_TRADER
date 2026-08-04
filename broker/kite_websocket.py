"""Kite Connect WebSocket streaming component for THETA AI TRADER.

Owns ``KiteTicker`` lifecycle, configurable multi-underlying subscriptions,
tick dispatch, health, and statistics. Does not perform OAuth, REST market
calls, or MarketSnapshot normalization.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol

from config.application_configuration import EnvironmentProfile

KITE_WEBSOCKET_VERSION: str = "1.1.0"
KITE_WEBSOCKET_SCHEMA_VERSION: str = "1.1.0"
PRODUCER_NAME: str = "broker.kite_websocket"

SUPPORTED_PRIMARY_UNDERLYINGS: frozenset[str] = frozenset(
    {"NIFTY", "BANKNIFTY", "SENSEX"}
)
SUPPORTED_SECONDARY_UNDERLYINGS: frozenset[str] = frozenset(
    {"FINNIFTY", "MIDCPNIFTY"}
)
SUPPORTED_UNDERLYINGS: frozenset[str] = (
    SUPPORTED_PRIMARY_UNDERLYINGS | SUPPORTED_SECONDARY_UNDERLYINGS
)

TOPIC_CONNECTION: str = "kite.ws.connection"
TOPIC_SUBSCRIPTION: str = "kite.ws.subscription"
TOPIC_TICK: str = "kite.ws.tick"
TOPIC_ERROR: str = "kite.ws.error"
TOPIC_HEALTH: str = "kite.ws.health"

_LOGGER = logging.getLogger(PRODUCER_NAME)


class WebSocketConnectionStatus(str, Enum):
    """Transport connection state."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DEGRADED = "degraded"
    FAILED = "failed"
    CLOSED = "closed"


class KiteWebSocketTickMode(str, Enum):
    """KiteTicker subscription mode."""

    FULL = "full"
    QUOTE = "quote"


class SubscriptionState(str, Enum):
    """Per-instrument subscription state."""

    PENDING = "pending"
    ACTIVE = "active"
    FAILED = "failed"
    UNSUBSCRIBED = "unsubscribed"


class UnderlyingSupportTier(str, Enum):
    """Catalog classification for a canonical underlying."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    EXPERIMENTAL = "experimental"


class WebSocketHealthStatus(str, Enum):
    """Aggregated WebSocket health."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class KiteWebSocketError(Exception):
    """Base error for Kite WebSocket operations."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        field: str | None = None,
        underlying: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.underlying = underlying


class KiteWebSocketConfigurationError(KiteWebSocketError):
    """Raised when WebSocket configuration is invalid."""


class KiteWebSocketConnectionError(KiteWebSocketError):
    """Raised when connection lifecycle operations fail."""


class KiteWebSocketSubscriptionError(KiteWebSocketError):
    """Raised when subscription apply operations fail."""


class KiteWebSocketValidationError(KiteWebSocketError):
    """Raised when instrument or state validation fails."""


@dataclass(frozen=True)
class WebSocketValidationIssue:
    """Immutable validation issue returned by :meth:`KiteWebSocketClient.validate`."""

    code: str
    message: str
    severity: str = "error"
    field: str | None = None
    underlying: str | None = None
    instrument_token: int | None = None


@dataclass(frozen=True)
class WebSocketHealthIssue:
    """Structured health issue with optional underlying attribution."""

    issue_code: str
    severity: str
    message: str
    underlying: str | None = None
    instrument_token: int | None = None


@dataclass(frozen=True)
class KiteWebSocketConfig:
    """Immutable WebSocket policy projected from Application Configuration."""

    environment_profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT
    enabled_underlyings: tuple[str, ...] = ("NIFTY",)
    tick_mode: KiteWebSocketTickMode = KiteWebSocketTickMode.FULL
    max_subscriptions: int = 500
    connect_timeout_seconds: float = 10.0
    heartbeat_silence_seconds: float = 5.0
    per_underlying_silence_seconds: float = 5.0
    allow_experimental_underlyings: bool = False
    publish_events: bool = False
    publish_tick_events: bool = False
    fail_closed_on_empty_instruments: bool = False
    runner_kind: str = "cli"
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        normalized = _normalize_underlyings(self.enabled_underlyings)
        object.__setattr__(self, "enabled_underlyings", normalized)
        if self.max_subscriptions <= 0:
            raise KiteWebSocketConfigurationError(
                "max_subscriptions must be > 0.",
                code="KITE_WS.CONFIG.INVALID",
                field="max_subscriptions",
            )
        if self.connect_timeout_seconds < 0:
            raise KiteWebSocketConfigurationError(
                "connect_timeout_seconds must be >= 0.",
                code="KITE_WS.CONFIG.INVALID",
                field="connect_timeout_seconds",
            )
        if self.heartbeat_silence_seconds < 0 or self.per_underlying_silence_seconds < 0:
            raise KiteWebSocketConfigurationError(
                "silence thresholds must be >= 0.",
                code="KITE_WS.CONFIG.INVALID",
                field="heartbeat_silence_seconds",
            )
        _validate_enabled_underlyings(
            normalized,
            allow_experimental=self.allow_experimental_underlyings,
        )
        if self.environment_profile is EnvironmentProfile.PRODUCTION:
            object.__setattr__(self, "fail_closed_on_empty_instruments", True)


@dataclass(frozen=True)
class SubscriptionInstrument:
    """Externally resolved instrument eligible for WebSocket subscription."""

    instrument_token: int
    underlying: str
    quote_key: str
    exchange: str
    tradingsymbol: str
    instrument_kind: str = "INDEX"
    mode: KiteWebSocketTickMode | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "underlying", str(self.underlying).strip().upper())
        if not isinstance(self.metadata, MappingProxyType):
            object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class SubscriptionRecord:
    """Immutable per-instrument subscription snapshot."""

    instrument_token: int
    underlying: str
    quote_key: str
    state: SubscriptionState
    mode: KiteWebSocketTickMode
    subscribed_at: datetime | None = None
    last_tick_at: datetime | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class SubscriptionDiff:
    """Desired-vs-active token diff for SDK apply."""

    subscribe_tokens: tuple[int, ...]
    unsubscribe_tokens: tuple[int, ...]
    mode_by_token: Mapping[int, KiteWebSocketTickMode]


@dataclass(frozen=True)
class UnderlyingStreamStats:
    """Per-underlying streaming statistics."""

    underlying: str
    support_tier: UnderlyingSupportTier
    desired_instrument_count: int
    active_instrument_count: int
    tick_count: int
    last_tick_at: datetime | None
    seconds_since_last_tick: float | None
    subscribe_success_count: int
    subscribe_failure_count: int
    error_count: int


@dataclass(frozen=True)
class WebSocketStatistics:
    """Global and per-underlying WebSocket statistics snapshot."""

    as_of: datetime
    connection_status: WebSocketConnectionStatus
    total_desired_instruments: int
    total_active_instruments: int
    total_tick_count: int
    last_tick_at: datetime | None
    reconnect_count: int
    handler_error_count: int
    enabled_underlyings: tuple[str, ...]
    per_underlying: tuple[UnderlyingStreamStats, ...]
    unattributed_tick_count: int = 0


@dataclass(frozen=True)
class WebSocketHealthReport:
    """Aggregated WebSocket health for multiple underlyings."""

    report_id: str
    as_of: datetime
    overall_health: WebSocketHealthStatus
    connection_status: WebSocketConnectionStatus
    enabled_underlyings: tuple[str, ...]
    healthy_underlyings: tuple[str, ...]
    degraded_underlyings: tuple[str, ...]
    unhealthy_underlyings: tuple[str, ...]
    active_subscription_count: int
    issues: tuple[WebSocketHealthIssue, ...]
    statistics: WebSocketStatistics


@dataclass(frozen=True)
class WebSocketConnectionEvent:
    """Connection status transition event."""

    event_id: str
    status: WebSocketConnectionStatus
    previous_status: WebSocketConnectionStatus
    enabled_underlyings: tuple[str, ...]
    occurred_at: datetime
    message: str = ""


@dataclass(frozen=True)
class WebSocketSubscriptionEvent:
    """Subscription apply event spanning one or more underlyings."""

    event_id: str
    underlyings: tuple[str, ...]
    subscribed_tokens: tuple[int, ...]
    unsubscribed_tokens: tuple[int, ...]
    subscribed_count: int
    unsubscribed_count: int
    per_underlying_subscribed_counts: Mapping[str, int]
    occurred_at: datetime
    correlation_id: str = ""


@dataclass(frozen=True)
class WebSocketTickEvent:
    """Optional Event Bus envelope for a single tick."""

    event_id: str
    instrument_token: int
    underlying: str | None
    tick: Mapping[str, Any]
    received_at: datetime


@dataclass(frozen=True)
class WebSocketErrorEvent:
    """Typed WebSocket error event."""

    event_id: str
    code: str
    message: str
    occurred_at: datetime
    underlying: str | None = None
    instrument_token: int | None = None


class _EventBusLike(Protocol):
    """Minimal Event Bus publish protocol."""

    def publish(self, topic: str, payload: object, **kwargs: Any) -> str:
        """Publish an event."""


TickerFactory = Callable[[str, str], Any]
TickHandler = Callable[[Mapping[str, Any]], None]
ErrorHandler = Callable[[WebSocketErrorEvent], None]
ConnectionHandler = Callable[[WebSocketConnectionEvent], None]


def _utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def _isoformat_utc(value: datetime | None) -> str | None:
    """Serialize timezone-aware datetime as ISO-8601 UTC with ``Z``."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_iso_datetime(raw: str) -> datetime:
    """Parse an ISO-8601 datetime string."""
    normalized = raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def normalize_underlying_name(name: str) -> str:
    """Normalize a canonical underlying name.

    Args:
        name: Raw underlying name.

    Returns:
        Uppercased stripped name.
    """
    return str(name).strip().upper()


def classify_underlying_tier(underlying: str) -> UnderlyingSupportTier:
    """Classify an underlying against the supported catalog.

    Args:
        underlying: Canonical underlying name.

    Returns:
        Support tier enumeration.
    """
    name = normalize_underlying_name(underlying)
    if name in SUPPORTED_PRIMARY_UNDERLYINGS:
        return UnderlyingSupportTier.PRIMARY
    if name in SUPPORTED_SECONDARY_UNDERLYINGS:
        return UnderlyingSupportTier.SECONDARY
    return UnderlyingSupportTier.EXPERIMENTAL


def _normalize_underlyings(values: Sequence[str]) -> tuple[str, ...]:
    """Normalize and validate uniqueness of enabled underlyings."""
    if not values:
        raise KiteWebSocketConfigurationError(
            "enabled_underlyings must contain at least one underlying.",
            code="KITE_WS.CONFIG.UNDERLYING_REQUIRED",
            field="enabled_underlyings",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        name = normalize_underlying_name(raw)
        if not name:
            raise KiteWebSocketConfigurationError(
                "enabled_underlyings entries must be non-empty.",
                code="KITE_WS.CONFIG.INVALID",
                field="enabled_underlyings",
            )
        if name in seen:
            raise KiteWebSocketConfigurationError(
                f"Duplicate underlying {name!r}.",
                code="KITE_WS.CONFIG.UNDERLYING_DUPLICATE",
                field="enabled_underlyings",
                underlying=name,
            )
        seen.add(name)
        normalized.append(name)
    return tuple(normalized)


def _validate_enabled_underlyings(
    underlyings: Sequence[str],
    *,
    allow_experimental: bool,
) -> None:
    """Validate underlyings against the supported catalog."""
    for name in underlyings:
        if name in SUPPORTED_UNDERLYINGS:
            continue
        if allow_experimental:
            continue
        raise KiteWebSocketConfigurationError(
            f"Unsupported underlying {name!r}.",
            code="KITE_WS.CONFIG.UNDERLYING_UNSUPPORTED",
            field="enabled_underlyings",
            underlying=name,
        )


def default_kite_websocket_config(
    profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT,
    *,
    enabled_underlyings: Sequence[str] = ("NIFTY",),
) -> KiteWebSocketConfig:
    """Build a profile-aware default WebSocket configuration.

    Args:
        profile: Environment profile.
        enabled_underlyings: Canonical underlyings from Application Configuration.

    Returns:
        Frozen :class:`KiteWebSocketConfig`.
    """
    return KiteWebSocketConfig(
        environment_profile=profile,
        enabled_underlyings=tuple(enabled_underlyings),
        tick_mode=KiteWebSocketTickMode.FULL,
        max_subscriptions=500,
        publish_events=profile is not EnvironmentProfile.PRODUCTION,
        publish_tick_events=False,
        fail_closed_on_empty_instruments=profile is EnvironmentProfile.PRODUCTION,
        runner_kind=profile.value,
    )


def _default_ticker_factory(api_key: str, access_token: str) -> Any:
    """Construct the official KiteTicker SDK object."""
    from kiteconnect import KiteTicker

    return KiteTicker(api_key, access_token)


def _copy_tick(tick: Mapping[str, Any]) -> Mapping[str, Any]:
    """Deep-copy an opaque tick mapping into an immutable proxy."""
    return MappingProxyType(deepcopy(dict(tick)))


def _mode_constant(ticker: Any, mode: KiteWebSocketTickMode) -> Any:
    """Resolve SDK mode constant when available, else return mode value."""
    if mode is KiteWebSocketTickMode.FULL:
        return getattr(ticker, "MODE_FULL", mode.value)
    return getattr(ticker, "MODE_QUOTE", mode.value)


class SubscriptionManager:
    """Thread-safe desired vs active subscription registry.

    Accepts configurable instrument lists resolved externally from Application
    Configuration and instrument master data. Never invents tokens.
    """

    def __init__(
        self,
        *,
        max_subscriptions: int,
        enabled_underlyings: Sequence[str],
        default_mode: KiteWebSocketTickMode = KiteWebSocketTickMode.FULL,
    ) -> None:
        """Initialize the subscription manager.

        Args:
            max_subscriptions: Maximum concurrent instrument tokens.
            enabled_underlyings: Canonical underlyings allowed in the desired set.
            default_mode: Default tick mode when instrument mode is omitted.
        """
        self._max_subscriptions = max_subscriptions
        self._enabled_underlyings = tuple(
            normalize_underlying_name(u) for u in enabled_underlyings
        )
        self._enabled_set = frozenset(self._enabled_underlyings)
        self._default_mode = default_mode
        self._lock = threading.RLock()
        self._desired: dict[int, SubscriptionInstrument] = {}
        self._active: dict[int, SubscriptionRecord] = {}

    @property
    def enabled_underlyings(self) -> tuple[str, ...]:
        """Return enabled underlyings in config order."""
        return self._enabled_underlyings

    def validate_instruments(
        self, instruments: Sequence[SubscriptionInstrument]
    ) -> None:
        """Validate instruments against enabled underlyings and limits.

        Args:
            instruments: Candidate instrument list.

        Raises:
            KiteWebSocketValidationError: When validation fails.
            KiteWebSocketSubscriptionError: When the limit would be exceeded.
        """
        seen: set[int] = set()
        for instrument in instruments:
            token = instrument.instrument_token
            if token <= 0:
                raise KiteWebSocketValidationError(
                    "instrument_token must be > 0.",
                    code="KITE_WS.VALIDATION.INVALID_TOKEN",
                    field="instrument_token",
                    underlying=instrument.underlying,
                )
            underlying = normalize_underlying_name(instrument.underlying)
            if underlying not in self._enabled_set:
                raise KiteWebSocketValidationError(
                    f"Underlying {underlying!r} is not enabled.",
                    code="KITE_WS.VALIDATION.UNDERLYING_NOT_ENABLED",
                    field="underlying",
                    underlying=underlying,
                )
            if not instrument.quote_key or not str(instrument.quote_key).strip():
                raise KiteWebSocketValidationError(
                    "quote_key must be non-empty.",
                    code="KITE_WS.VALIDATION.INVALID_TOKEN",
                    field="quote_key",
                    underlying=underlying,
                )
            if token in seen:
                raise KiteWebSocketValidationError(
                    f"Duplicate instrument_token {token}.",
                    code="KITE_WS.VALIDATION.DUPLICATE_TOKEN",
                    field="instrument_token",
                    underlying=underlying,
                )
            seen.add(token)
        if len(instruments) > self._max_subscriptions:
            raise KiteWebSocketSubscriptionError(
                "Desired instrument count exceeds max_subscriptions.",
                code="KITE_WS.SUBSCRIBE.LIMIT_EXCEEDED",
                field="instruments",
            )

    def set_instruments(self, instruments: Sequence[SubscriptionInstrument]) -> None:
        """Replace the desired configurable instrument list.

        Args:
            instruments: Resolved instruments for enabled underlyings.
        """
        self.validate_instruments(instruments)
        with self._lock:
            self._desired = {
                instrument.instrument_token: replace(
                    instrument,
                    underlying=normalize_underlying_name(instrument.underlying),
                )
                for instrument in instruments
            }

    def add_instruments(self, instruments: Sequence[SubscriptionInstrument]) -> None:
        """Merge instruments into the desired set.

        Args:
            instruments: Additional resolved instruments.
        """
        with self._lock:
            merged = dict(self._desired)
            for instrument in instruments:
                merged[instrument.instrument_token] = replace(
                    instrument,
                    underlying=normalize_underlying_name(instrument.underlying),
                )
            ordered = tuple(
                merged[token] for token in sorted(merged.keys())
            )
            self.validate_instruments(ordered)
            self._desired = {
                instrument.instrument_token: instrument for instrument in ordered
            }

    def remove_tokens(self, tokens: Sequence[int]) -> None:
        """Remove tokens from the desired set.

        Args:
            tokens: Instrument tokens to remove.
        """
        with self._lock:
            for token in tokens:
                self._desired.pop(int(token), None)

    def clear(self) -> None:
        """Clear the desired instrument set."""
        with self._lock:
            self._desired.clear()

    def desired(self) -> tuple[SubscriptionInstrument, ...]:
        """Return desired instruments ordered by token."""
        with self._lock:
            return tuple(
                self._desired[token] for token in sorted(self._desired.keys())
            )

    def active(self) -> tuple[SubscriptionRecord, ...]:
        """Return active subscription records ordered by token."""
        with self._lock:
            return tuple(
                self._active[token] for token in sorted(self._active.keys())
            )

    def diff(self) -> SubscriptionDiff:
        """Compute subscribe/unsubscribe token sets.

        Returns:
            Immutable :class:`SubscriptionDiff`.
        """
        with self._lock:
            desired_tokens = set(self._desired.keys())
            active_tokens = {
                token
                for token, record in self._active.items()
                if record.state is SubscriptionState.ACTIVE
            }
            subscribe = tuple(sorted(desired_tokens - active_tokens))
            unsubscribe = tuple(sorted(active_tokens - desired_tokens))
            mode_by_token: dict[int, KiteWebSocketTickMode] = {}
            for token in subscribe:
                instrument = self._desired[token]
                mode_by_token[token] = instrument.mode or self._default_mode
            return SubscriptionDiff(
                subscribe_tokens=subscribe,
                unsubscribe_tokens=unsubscribe,
                mode_by_token=MappingProxyType(mode_by_token),
            )

    def mark_active(self, tokens: Sequence[int], *, at: datetime) -> None:
        """Mark tokens as ACTIVE after successful SDK subscribe.

        Args:
            tokens: Successfully subscribed tokens.
            at: Subscription timestamp.
        """
        with self._lock:
            for token in tokens:
                instrument = self._desired.get(token)
                if instrument is None:
                    continue
                mode = instrument.mode or self._default_mode
                previous = self._active.get(token)
                self._active[token] = SubscriptionRecord(
                    instrument_token=token,
                    underlying=instrument.underlying,
                    quote_key=instrument.quote_key,
                    state=SubscriptionState.ACTIVE,
                    mode=mode,
                    subscribed_at=at,
                    last_tick_at=previous.last_tick_at if previous else None,
                    error_code=None,
                )

    def mark_failed(
        self,
        tokens: Sequence[int],
        *,
        error_code: str,
        at: datetime | None = None,
    ) -> None:
        """Mark tokens as FAILED after SDK subscribe errors.

        Args:
            tokens: Failed tokens.
            error_code: Stable error code.
            at: Optional timestamp retained on existing records.
        """
        with self._lock:
            for token in tokens:
                instrument = self._desired.get(token)
                if instrument is None:
                    continue
                mode = instrument.mode or self._default_mode
                previous = self._active.get(token)
                self._active[token] = SubscriptionRecord(
                    instrument_token=token,
                    underlying=instrument.underlying,
                    quote_key=instrument.quote_key,
                    state=SubscriptionState.FAILED,
                    mode=mode,
                    subscribed_at=previous.subscribed_at if previous else at,
                    last_tick_at=previous.last_tick_at if previous else None,
                    error_code=error_code,
                )

    def mark_unsubscribed(self, tokens: Sequence[int]) -> None:
        """Remove tokens from the active registry after unsubscribe.

        Args:
            tokens: Tokens removed from the SDK subscription set.
        """
        with self._lock:
            for token in tokens:
                self._active.pop(int(token), None)

    def clear_active(self) -> None:
        """Clear active registry while retaining the desired set."""
        with self._lock:
            self._active.clear()

    def records_for_underlying(self, underlying: str) -> tuple[SubscriptionRecord, ...]:
        """Return active records for one underlying.

        Args:
            underlying: Canonical underlying name.

        Returns:
            Matching subscription records ordered by token.
        """
        name = normalize_underlying_name(underlying)
        with self._lock:
            return tuple(
                record
                for token in sorted(self._active.keys())
                if (record := self._active[token]).underlying == name
            )

    def note_tick(self, token: int, *, at: datetime) -> str | None:
        """Update last-tick timestamp for an active token.

        Args:
            token: Instrument token from the tick.
            at: Tick receive time.

        Returns:
            Underlying name when known, otherwise ``None``.
        """
        with self._lock:
            record = self._active.get(token)
            if record is None:
                desired = self._desired.get(token)
                return desired.underlying if desired else None
            self._active[token] = replace(record, last_tick_at=at)
            return record.underlying

    def underlying_for_token(self, token: int) -> str | None:
        """Resolve underlying for a token from active or desired sets."""
        with self._lock:
            record = self._active.get(token)
            if record is not None:
                return record.underlying
            instrument = self._desired.get(token)
            return instrument.underlying if instrument else None

    def active_tokens(self) -> frozenset[int]:
        """Return the set of ACTIVE tokens."""
        with self._lock:
            return frozenset(
                token
                for token, record in self._active.items()
                if record.state is SubscriptionState.ACTIVE
            )


class KiteWebSocketClient:
    """Public facade for KiteTicker streaming with multi-underlying support."""

    def __init__(
        self,
        config: KiteWebSocketConfig,
        *,
        api_key: str,
        access_token: str,
        event_bus: _EventBusLike | None = None,
        clock: Callable[[], datetime] | None = None,
        ticker_factory: TickerFactory | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        """Construct a WebSocket client.

        Args:
            config: Frozen WebSocket configuration.
            api_key: Kite API key from an authenticated session.
            access_token: Kite access token from an authenticated session.
            event_bus: Optional Event Bus for ``kite.ws.*`` topics.
            clock: Injectable clock for deterministic tests.
            ticker_factory: Injectable ``KiteTicker`` factory.
            id_factory: Injectable UUID factory for events/reports.

        Raises:
            KiteWebSocketConfigurationError: When credentials or config are invalid.
        """
        if not api_key or not str(api_key).strip():
            raise KiteWebSocketConfigurationError(
                "api_key is required.",
                code="KITE_WS.CONFIG.INVALID",
                field="api_key",
            )
        if not access_token or not str(access_token).strip():
            raise KiteWebSocketConfigurationError(
                "access_token is required.",
                code="KITE_WS.CONFIG.INVALID",
                field="access_token",
            )
        self._config = config
        self._api_key = str(api_key).strip()
        self._access_token = str(access_token).strip()
        self._event_bus = event_bus
        self._clock = clock or _utc_now
        self._ticker_factory = ticker_factory or _default_ticker_factory
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._lock = threading.RLock()
        self._status = WebSocketConnectionStatus.DISCONNECTED
        self._ever_connected = False
        self._ticker: Any | None = None
        self._tick_handler: TickHandler | None = None
        self._error_handler: ErrorHandler | None = None
        self._connection_handler: ConnectionHandler | None = None
        self._subscriptions = SubscriptionManager(
            max_subscriptions=config.max_subscriptions,
            enabled_underlyings=config.enabled_underlyings,
            default_mode=config.tick_mode,
        )
        self._total_tick_count = 0
        self._unattributed_tick_count = 0
        self._last_tick_at: datetime | None = None
        self._reconnect_count = 0
        self._handler_error_count = 0
        self._tick_count_by_underlying: dict[str, int] = {
            u: 0 for u in config.enabled_underlyings
        }
        self._last_tick_by_underlying: dict[str, datetime | None] = {
            u: None for u in config.enabled_underlyings
        }
        self._subscribe_success_by_underlying: dict[str, int] = {
            u: 0 for u in config.enabled_underlyings
        }
        self._subscribe_failure_by_underlying: dict[str, int] = {
            u: 0 for u in config.enabled_underlyings
        }
        self._error_count_by_underlying: dict[str, int] = {
            u: 0 for u in config.enabled_underlyings
        }
        self._last_applied_desired_tokens: frozenset[int] = frozenset()

    def __repr__(self) -> str:
        return (
            f"KiteWebSocketClient(status={self._status!r}, "
            f"api_key='<redacted>', access_token='<redacted>', "
            f"enabled_underlyings={self._config.enabled_underlyings!r})"
        )

    def enabled_underlyings(self) -> tuple[str, ...]:
        """Return normalized configured underlyings."""
        return self._config.enabled_underlyings

    def get_status(self) -> WebSocketConnectionStatus:
        """Return the current connection status."""
        with self._lock:
            return self._status

    def set_tick_handler(self, handler: TickHandler | None) -> None:
        """Register or clear the tick callback."""
        with self._lock:
            self._tick_handler = handler

    def set_error_handler(self, handler: ErrorHandler | None) -> None:
        """Register or clear the error callback."""
        with self._lock:
            self._error_handler = handler

    def set_connection_handler(self, handler: ConnectionHandler | None) -> None:
        """Register or clear the connection callback."""
        with self._lock:
            self._connection_handler = handler

    def set_instruments(self, instruments: Sequence[SubscriptionInstrument]) -> None:
        """Replace the desired configurable instrument list.

        Args:
            instruments: Externally resolved instruments.
        """
        self._subscriptions.set_instruments(instruments)

    def get_subscriptions(self) -> tuple[SubscriptionRecord, ...]:
        """Return an immutable active subscription snapshot."""
        return self._subscriptions.active()

    def get_instruments_for_underlying(
        self, underlying: str
    ) -> tuple[SubscriptionRecord, ...]:
        """Return active subscriptions for one underlying."""
        return self._subscriptions.records_for_underlying(underlying)

    def connect(self) -> None:
        """Open the KiteTicker connection."""
        with self._lock:
            if self._status in {
                WebSocketConnectionStatus.CONNECTED,
                WebSocketConnectionStatus.CONNECTING,
            }:
                return
            previous = self._status
            self._status = WebSocketConnectionStatus.CONNECTING
        try:
            ticker = self._ticker_factory(self._api_key, self._access_token)
            self._register_callbacks(ticker)
            with self._lock:
                self._ticker = ticker
            connect = getattr(ticker, "connect", None)
            if not callable(connect):
                raise KiteWebSocketConnectionError(
                    "Ticker factory did not provide connect().",
                    code="KITE_WS.CONNECTION.FAILED",
                )
            connect(threaded=True)
            # Deterministic path for FakeKiteTicker that connects synchronously.
            if getattr(ticker, "connected", False):
                self._on_connect(ticker)
            elif self.get_status() is WebSocketConnectionStatus.CONNECTING:
                # Keep CONNECTING until on_connect; tests may call on_connect.
                self._emit_connection(previous, WebSocketConnectionStatus.CONNECTING)
        except KiteWebSocketError:
            with self._lock:
                self._status = WebSocketConnectionStatus.FAILED
            raise
        except Exception as exc:
            with self._lock:
                self._status = WebSocketConnectionStatus.FAILED
            raise KiteWebSocketConnectionError(
                f"WebSocket connect failed: {exc}",
                code="KITE_WS.CONNECTION.FAILED",
            ) from exc

    def disconnect(self) -> None:
        """Close the WebSocket connection and clear the active registry."""
        with self._lock:
            previous = self._status
            ticker = self._ticker
        if ticker is not None:
            close = getattr(ticker, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # noqa: BLE001
                    self._emit_error(
                        code="KITE_WS.CONNECTION.FAILED",
                        message=f"Disconnect close failed: {exc}",
                    )
        self._subscriptions.clear_active()
        with self._lock:
            self._ticker = None
            self._status = WebSocketConnectionStatus.CLOSED
            self._last_applied_desired_tokens = frozenset()
        self._emit_connection(previous, WebSocketConnectionStatus.CLOSED)

    def apply_subscriptions(self) -> WebSocketSubscriptionEvent:
        """Diff desired vs active subscriptions and apply via the SDK.

        Returns:
            Immutable subscription event covering affected underlyings.

        Raises:
            KiteWebSocketConnectionError: When not connected.
            KiteWebSocketSubscriptionError: When SDK apply fails.
            KiteWebSocketValidationError: When fail-closed empty universe applies.
        """
        with self._lock:
            status = self._status
            ticker = self._ticker
            now = self._clock()
        if status is not WebSocketConnectionStatus.CONNECTED or ticker is None:
            raise KiteWebSocketConnectionError(
                "Cannot subscribe while disconnected.",
                code="KITE_WS.CONNECTION.NOT_CONNECTED",
            )

        desired = self._subscriptions.desired()
        diff = self._subscriptions.diff()
        subscribed: list[int] = []
        unsubscribed: list[int] = []
        affected: set[str] = set()
        per_underlying_counts: dict[str, int] = {
            u: 0 for u in self._config.enabled_underlyings
        }

        if diff.unsubscribe_tokens:
            try:
                ticker.unsubscribe(list(diff.unsubscribe_tokens))
            except Exception as exc:
                raise KiteWebSocketSubscriptionError(
                    f"Unsubscribe failed: {exc}",
                    code="KITE_WS.SUBSCRIBE.SDK_FAILED",
                ) from exc
            self._subscriptions.mark_unsubscribed(diff.unsubscribe_tokens)
            unsubscribed.extend(diff.unsubscribe_tokens)
            for token in diff.unsubscribe_tokens:
                underlying = self._subscriptions.underlying_for_token(token)
                if underlying:
                    affected.add(underlying)

        if diff.subscribe_tokens:
            try:
                ticker.subscribe(list(diff.subscribe_tokens))
                # Group tokens by mode for set_mode.
                mode_groups: dict[KiteWebSocketTickMode, list[int]] = {}
                for token in diff.subscribe_tokens:
                    mode = diff.mode_by_token[token]
                    mode_groups.setdefault(mode, []).append(token)
                set_mode = getattr(ticker, "set_mode", None)
                if callable(set_mode):
                    for mode, tokens in mode_groups.items():
                        set_mode(_mode_constant(ticker, mode), tokens)
            except Exception as exc:
                self._subscriptions.mark_failed(
                    diff.subscribe_tokens,
                    error_code="KITE_WS.SUBSCRIBE.SDK_FAILED",
                    at=now,
                )
                for token in diff.subscribe_tokens:
                    underlying = self._subscriptions.underlying_for_token(token)
                    if underlying:
                        with self._lock:
                            self._subscribe_failure_by_underlying[underlying] = (
                                self._subscribe_failure_by_underlying.get(underlying, 0)
                                + 1
                            )
                            self._error_count_by_underlying[underlying] = (
                                self._error_count_by_underlying.get(underlying, 0) + 1
                            )
                raise KiteWebSocketSubscriptionError(
                    f"Subscribe failed: {exc}",
                    code="KITE_WS.SUBSCRIBE.SDK_FAILED",
                ) from exc

            self._subscriptions.mark_active(diff.subscribe_tokens, at=now)
            subscribed.extend(diff.subscribe_tokens)
            for token in diff.subscribe_tokens:
                underlying = self._subscriptions.underlying_for_token(token)
                if underlying:
                    affected.add(underlying)
                    per_underlying_counts[underlying] = (
                        per_underlying_counts.get(underlying, 0) + 1
                    )
                    with self._lock:
                        self._subscribe_success_by_underlying[underlying] = (
                            self._subscribe_success_by_underlying.get(underlying, 0)
                            + 1
                        )

        with self._lock:
            self._last_applied_desired_tokens = frozenset(
                instrument.instrument_token for instrument in desired
            )

        ordered_underlyings = tuple(
            u for u in self._config.enabled_underlyings if u in affected
        ) or tuple(sorted(affected))
        event = WebSocketSubscriptionEvent(
            event_id=self._id_factory(),
            underlyings=ordered_underlyings,
            subscribed_tokens=tuple(sorted(subscribed)),
            unsubscribed_tokens=tuple(sorted(unsubscribed)),
            subscribed_count=len(subscribed),
            unsubscribed_count=len(unsubscribed),
            per_underlying_subscribed_counts=MappingProxyType(
                {k: v for k, v in per_underlying_counts.items() if v > 0}
            ),
            occurred_at=now,
        )
        self._publish(TOPIC_SUBSCRIPTION, event)
        return event

    def validate(self) -> tuple[WebSocketValidationIssue, ...]:
        """Validate config, desired instruments, and connection invariants.

        Returns:
            Immutable validation issues (empty when valid).
        """
        issues: list[WebSocketValidationIssue] = []
        for underlying in self._config.enabled_underlyings:
            if (
                underlying not in SUPPORTED_UNDERLYINGS
                and not self._config.allow_experimental_underlyings
            ):
                issues.append(
                    WebSocketValidationIssue(
                        code="KITE_WS.CONFIG.UNDERLYING_UNSUPPORTED",
                        message=f"Unsupported underlying {underlying!r}.",
                        underlying=underlying,
                        field="enabled_underlyings",
                    )
                )
            elif classify_underlying_tier(underlying) is UnderlyingSupportTier.SECONDARY:
                issues.append(
                    WebSocketValidationIssue(
                        code="KITE_WS.CONFIG.INVALID",
                        message=f"Secondary underlying {underlying!r} enabled.",
                        severity="info",
                        underlying=underlying,
                        field="enabled_underlyings",
                    )
                )

        desired = self._subscriptions.desired()
        tokens: set[int] = set()
        for instrument in desired:
            if instrument.instrument_token <= 0:
                issues.append(
                    WebSocketValidationIssue(
                        code="KITE_WS.VALIDATION.INVALID_TOKEN",
                        message="instrument_token must be > 0.",
                        underlying=instrument.underlying,
                        instrument_token=instrument.instrument_token,
                        field="instrument_token",
                    )
                )
            if instrument.underlying not in self._config.enabled_underlyings:
                issues.append(
                    WebSocketValidationIssue(
                        code="KITE_WS.VALIDATION.UNDERLYING_NOT_ENABLED",
                        message="Instrument underlying is not enabled.",
                        underlying=instrument.underlying,
                        instrument_token=instrument.instrument_token,
                    )
                )
            if not instrument.quote_key.strip():
                issues.append(
                    WebSocketValidationIssue(
                        code="KITE_WS.VALIDATION.INVALID_TOKEN",
                        message="quote_key must be non-empty.",
                        underlying=instrument.underlying,
                        instrument_token=instrument.instrument_token,
                        field="quote_key",
                    )
                )
            if instrument.instrument_token in tokens:
                issues.append(
                    WebSocketValidationIssue(
                        code="KITE_WS.VALIDATION.DUPLICATE_TOKEN",
                        message="Duplicate instrument token in desired set.",
                        underlying=instrument.underlying,
                        instrument_token=instrument.instrument_token,
                    )
                )
            tokens.add(instrument.instrument_token)

        if len(desired) > self._config.max_subscriptions:
            issues.append(
                WebSocketValidationIssue(
                    code="KITE_WS.SUBSCRIBE.LIMIT_EXCEEDED",
                    message="Desired instrument count exceeds max_subscriptions.",
                    field="instruments",
                )
            )

        if not desired and self._config.fail_closed_on_empty_instruments:
            issues.append(
                WebSocketValidationIssue(
                    code="KITE_WS.VALIDATION.NO_INSTRUMENTS",
                    message="No instruments resolved for enabled underlyings.",
                    severity="error",
                )
            )

        with self._lock:
            status = self._status
            last_applied = self._last_applied_desired_tokens
        if status is WebSocketConnectionStatus.CONNECTED:
            active_tokens = self._subscriptions.active_tokens()
            if not active_tokens.issubset(last_applied | set(tokens)):
                issues.append(
                    WebSocketValidationIssue(
                        code="KITE_WS.HEALTH.TOKEN_MISSING",
                        message="Active set is inconsistent with last applied desired set.",
                        severity="warning",
                    )
                )

        return tuple(issues)

    def get_statistics(self) -> WebSocketStatistics:
        """Return global and per-underlying statistics."""
        now = self._clock()
        desired = self._subscriptions.desired()
        active = self._subscriptions.active()
        desired_by_ul: dict[str, int] = {u: 0 for u in self._config.enabled_underlyings}
        active_by_ul: dict[str, int] = {u: 0 for u in self._config.enabled_underlyings}
        for instrument in desired:
            desired_by_ul[instrument.underlying] = (
                desired_by_ul.get(instrument.underlying, 0) + 1
            )
        for record in active:
            if record.state is SubscriptionState.ACTIVE:
                active_by_ul[record.underlying] = (
                    active_by_ul.get(record.underlying, 0) + 1
                )

        with self._lock:
            status = self._status
            total_ticks = self._total_tick_count
            last_tick = self._last_tick_at
            reconnects = self._reconnect_count
            handler_errors = self._handler_error_count
            unattributed = self._unattributed_tick_count
            per_underlying: list[UnderlyingStreamStats] = []
            for underlying in self._config.enabled_underlyings:
                last = self._last_tick_by_underlying.get(underlying)
                silence = (now - last).total_seconds() if last is not None else None
                per_underlying.append(
                    UnderlyingStreamStats(
                        underlying=underlying,
                        support_tier=classify_underlying_tier(underlying),
                        desired_instrument_count=desired_by_ul.get(underlying, 0),
                        active_instrument_count=active_by_ul.get(underlying, 0),
                        tick_count=self._tick_count_by_underlying.get(underlying, 0),
                        last_tick_at=last,
                        seconds_since_last_tick=silence,
                        subscribe_success_count=self._subscribe_success_by_underlying.get(
                            underlying, 0
                        ),
                        subscribe_failure_count=self._subscribe_failure_by_underlying.get(
                            underlying, 0
                        ),
                        error_count=self._error_count_by_underlying.get(underlying, 0),
                    )
                )

        return WebSocketStatistics(
            as_of=now,
            connection_status=status,
            total_desired_instruments=len(desired),
            total_active_instruments=sum(
                1 for r in active if r.state is SubscriptionState.ACTIVE
            ),
            total_tick_count=total_ticks,
            last_tick_at=last_tick,
            reconnect_count=reconnects,
            handler_error_count=handler_errors,
            enabled_underlyings=self._config.enabled_underlyings,
            per_underlying=tuple(per_underlying),
            unattributed_tick_count=unattributed,
        )

    def reset_statistics(self) -> None:
        """Zero counters while retaining configuration identity."""
        with self._lock:
            self._total_tick_count = 0
            self._unattributed_tick_count = 0
            self._last_tick_at = None
            self._reconnect_count = 0
            self._handler_error_count = 0
            for underlying in self._config.enabled_underlyings:
                self._tick_count_by_underlying[underlying] = 0
                self._last_tick_by_underlying[underlying] = None
                self._subscribe_success_by_underlying[underlying] = 0
                self._subscribe_failure_by_underlying[underlying] = 0
                self._error_count_by_underlying[underlying] = 0

    def get_health(self) -> WebSocketHealthReport:
        """Return global and per-underlying health."""
        stats = self.get_statistics()
        now = stats.as_of
        issues: list[WebSocketHealthIssue] = []
        healthy: list[str] = []
        degraded: list[str] = []
        unhealthy: list[str] = []

        with self._lock:
            status = self._status
            ever = self._ever_connected

        for entry in stats.per_underlying:
            underlying = entry.underlying
            silence = entry.seconds_since_last_tick
            if entry.desired_instrument_count == 0:
                # Enabled but unresolved — unknown locally; treat as degraded signal.
                degraded.append(underlying)
                issues.append(
                    WebSocketHealthIssue(
                        issue_code="KITE_WS.HEALTH.UNDERLYING_UNSUBSCRIBED",
                        severity="warning",
                        message=f"No instruments resolved for {underlying}.",
                        underlying=underlying,
                    )
                )
                continue

            if entry.active_instrument_count == 0:
                unhealthy.append(underlying)
                issues.append(
                    WebSocketHealthIssue(
                        issue_code="KITE_WS.HEALTH.UNDERLYING_UNSUBSCRIBED",
                        severity="error",
                        message=f"No active subscriptions for {underlying}.",
                        underlying=underlying,
                    )
                )
                continue

            silent = (
                silence is not None
                and silence >= self._config.per_underlying_silence_seconds
            )
            partial = entry.active_instrument_count < entry.desired_instrument_count
            if silent:
                degraded.append(underlying)
                issues.append(
                    WebSocketHealthIssue(
                        issue_code="KITE_WS.HEALTH.UNDERLYING_SILENT",
                        severity="warning",
                        message=f"Underlying {underlying} is silent.",
                        underlying=underlying,
                    )
                )
            elif partial:
                degraded.append(underlying)
                issues.append(
                    WebSocketHealthIssue(
                        issue_code="KITE_WS.HEALTH.PARTIAL_COVERAGE",
                        severity="warning",
                        message=f"Partial coverage for {underlying}.",
                        underlying=underlying,
                    )
                )
            else:
                healthy.append(underlying)

        if status in {
            WebSocketConnectionStatus.DISCONNECTED,
            WebSocketConnectionStatus.FAILED,
            WebSocketConnectionStatus.CLOSED,
        }:
            if not ever and status is WebSocketConnectionStatus.DISCONNECTED:
                overall = WebSocketHealthStatus.UNKNOWN
            else:
                overall = WebSocketHealthStatus.UNHEALTHY
        elif status is WebSocketConnectionStatus.CONNECTED:
            if unhealthy and not healthy and not degraded:
                overall = WebSocketHealthStatus.UNHEALTHY
            elif unhealthy or degraded:
                overall = WebSocketHealthStatus.DEGRADED
            elif healthy:
                overall = WebSocketHealthStatus.HEALTHY
            else:
                overall = WebSocketHealthStatus.UNKNOWN
        elif status in {
            WebSocketConnectionStatus.RECONNECTING,
            WebSocketConnectionStatus.DEGRADED,
            WebSocketConnectionStatus.CONNECTING,
        }:
            overall = WebSocketHealthStatus.DEGRADED
        else:
            overall = WebSocketHealthStatus.UNKNOWN

        report = WebSocketHealthReport(
            report_id=self._id_factory(),
            as_of=now,
            overall_health=overall,
            connection_status=status,
            enabled_underlyings=self._config.enabled_underlyings,
            healthy_underlyings=tuple(healthy),
            degraded_underlyings=tuple(degraded),
            unhealthy_underlyings=tuple(unhealthy),
            active_subscription_count=stats.total_active_instruments,
            issues=tuple(issues),
            statistics=stats,
        )
        if self._config.publish_events:
            self._publish(TOPIC_HEALTH, report)
        return report

    def _register_callbacks(self, ticker: Any) -> None:
        """Wire SDK callbacks onto the ticker instance."""

        def on_ticks(_ws: Any, ticks: Sequence[Mapping[str, Any]]) -> None:
            self._on_ticks(ticks)

        def on_connect(ws: Any, *args: Any) -> None:
            self._on_connect(ws)

        def on_close(_ws: Any, *args: Any) -> None:
            self._on_close()

        def on_error(_ws: Any, code: Any, reason: Any) -> None:
            self._on_error(code, reason)

        def on_reconnect(_ws: Any, attempts: Any = 0) -> None:
            self._on_reconnect(int(attempts) if attempts is not None else 0)

        def on_noreconnect(_ws: Any) -> None:
            self._on_noreconnect()

        ticker.on_ticks = on_ticks
        ticker.on_connect = on_connect
        ticker.on_close = on_close
        ticker.on_error = on_error
        ticker.on_reconnect = on_reconnect
        ticker.on_noreconnect = on_noreconnect

    def _on_connect(self, _ticker: Any) -> None:
        with self._lock:
            previous = self._status
            self._status = WebSocketConnectionStatus.CONNECTED
            self._ever_connected = True
        self._emit_connection(previous, WebSocketConnectionStatus.CONNECTED)

    def _on_close(self) -> None:
        with self._lock:
            previous = self._status
            self._status = WebSocketConnectionStatus.CLOSED
        self._subscriptions.clear_active()
        self._emit_connection(previous, WebSocketConnectionStatus.CLOSED)

    def _on_error(self, code: Any, reason: Any) -> None:
        message = f"WebSocket error code={code} reason={reason}"
        self._emit_error(code="KITE_WS.CONNECTION.FAILED", message=message)
        with self._lock:
            if self._status is WebSocketConnectionStatus.CONNECTED:
                self._status = WebSocketConnectionStatus.DEGRADED

    def _on_reconnect(self, attempts: int) -> None:
        with self._lock:
            previous = self._status
            self._status = WebSocketConnectionStatus.RECONNECTING
            self._reconnect_count += 1
        self._emit_connection(
            previous,
            WebSocketConnectionStatus.RECONNECTING,
            message=f"reconnect attempt {attempts}",
        )

    def _on_noreconnect(self) -> None:
        with self._lock:
            previous = self._status
            self._status = WebSocketConnectionStatus.FAILED
        self._emit_connection(previous, WebSocketConnectionStatus.FAILED, message="noreconnect")
        self._emit_error(
            code="KITE_WS.CONNECTION.FAILED",
            message="KiteTicker noreconnect.",
        )

    def _on_ticks(self, ticks: Sequence[Mapping[str, Any]]) -> None:
        now = self._clock()
        handler: TickHandler | None
        with self._lock:
            handler = self._tick_handler
        for raw in ticks:
            tick = _copy_tick(raw)
            token_raw = tick.get("instrument_token")
            try:
                token = int(token_raw) if token_raw is not None else -1
            except (TypeError, ValueError):
                token = -1
            underlying = self._subscriptions.note_tick(token, at=now) if token > 0 else None
            with self._lock:
                self._total_tick_count += 1
                self._last_tick_at = now
                if underlying is None:
                    self._unattributed_tick_count += 1
                else:
                    self._tick_count_by_underlying[underlying] = (
                        self._tick_count_by_underlying.get(underlying, 0) + 1
                    )
                    self._last_tick_by_underlying[underlying] = now

            if handler is not None:
                try:
                    handler(tick)
                except Exception:  # noqa: BLE001
                    with self._lock:
                        self._handler_error_count += 1
                        if underlying:
                            self._error_count_by_underlying[underlying] = (
                                self._error_count_by_underlying.get(underlying, 0) + 1
                            )
                    _LOGGER.exception("kite_ws.tick.handler_failed")

            if (
                self._config.publish_events
                and self._config.publish_tick_events
                and self._config.environment_profile is not EnvironmentProfile.PRODUCTION
            ):
                event = WebSocketTickEvent(
                    event_id=self._id_factory(),
                    instrument_token=token,
                    underlying=underlying,
                    tick=tick,
                    received_at=now,
                )
                self._publish(TOPIC_TICK, event)

    def _emit_connection(
        self,
        previous: WebSocketConnectionStatus,
        status: WebSocketConnectionStatus,
        *,
        message: str = "",
    ) -> None:
        event = WebSocketConnectionEvent(
            event_id=self._id_factory(),
            status=status,
            previous_status=previous,
            enabled_underlyings=self._config.enabled_underlyings,
            occurred_at=self._clock(),
            message=message,
        )
        handler: ConnectionHandler | None
        with self._lock:
            handler = self._connection_handler
        if handler is not None:
            try:
                handler(event)
            except Exception:  # noqa: BLE001
                with self._lock:
                    self._handler_error_count += 1
                _LOGGER.exception("kite_ws.connection.handler_failed")
        self._publish(TOPIC_CONNECTION, event)

    def _emit_error(
        self,
        *,
        code: str,
        message: str,
        underlying: str | None = None,
        instrument_token: int | None = None,
    ) -> None:
        event = WebSocketErrorEvent(
            event_id=self._id_factory(),
            code=code,
            message=message,
            occurred_at=self._clock(),
            underlying=underlying,
            instrument_token=instrument_token,
        )
        handler: ErrorHandler | None
        with self._lock:
            handler = self._error_handler
            if underlying:
                self._error_count_by_underlying[underlying] = (
                    self._error_count_by_underlying.get(underlying, 0) + 1
                )
        if handler is not None:
            try:
                handler(event)
            except Exception:  # noqa: BLE001
                with self._lock:
                    self._handler_error_count += 1
                _LOGGER.exception("kite_ws.error.handler_failed")
        self._publish(TOPIC_ERROR, event)

    def _publish(self, topic: str, payload: object) -> None:
        if not self._config.publish_events or self._event_bus is None:
            return
        try:
            self._event_bus.publish(
                topic,
                payload,
                producer=PRODUCER_NAME,
                producer_version=KITE_WEBSOCKET_VERSION,
                correlation_id=self._id_factory(),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("kite_ws.event.publish_failed topic=%s", topic)


def serialize_subscription_records(records: Sequence[SubscriptionRecord]) -> str:
    """Serialize subscription records to JSON."""
    payload = {
        "schema_version": KITE_WEBSOCKET_SCHEMA_VERSION,
        "records": [
            {
                "instrument_token": record.instrument_token,
                "underlying": record.underlying,
                "quote_key": record.quote_key,
                "state": record.state.value,
                "mode": record.mode.value,
                "subscribed_at": _isoformat_utc(record.subscribed_at),
                "last_tick_at": _isoformat_utc(record.last_tick_at),
                "error_code": record.error_code,
            }
            for record in records
        ],
    }
    return json.dumps(payload, sort_keys=True)


def serialize_websocket_statistics(stats: WebSocketStatistics) -> str:
    """Serialize WebSocket statistics to JSON."""
    payload = {
        "schema_version": KITE_WEBSOCKET_SCHEMA_VERSION,
        "as_of": _isoformat_utc(stats.as_of),
        "connection_status": stats.connection_status.value,
        "total_desired_instruments": stats.total_desired_instruments,
        "total_active_instruments": stats.total_active_instruments,
        "total_tick_count": stats.total_tick_count,
        "last_tick_at": _isoformat_utc(stats.last_tick_at),
        "reconnect_count": stats.reconnect_count,
        "handler_error_count": stats.handler_error_count,
        "enabled_underlyings": list(stats.enabled_underlyings),
        "unattributed_tick_count": stats.unattributed_tick_count,
        "per_underlying": [
            {
                "underlying": entry.underlying,
                "support_tier": entry.support_tier.value,
                "desired_instrument_count": entry.desired_instrument_count,
                "active_instrument_count": entry.active_instrument_count,
                "tick_count": entry.tick_count,
                "last_tick_at": _isoformat_utc(entry.last_tick_at),
                "seconds_since_last_tick": entry.seconds_since_last_tick,
                "subscribe_success_count": entry.subscribe_success_count,
                "subscribe_failure_count": entry.subscribe_failure_count,
                "error_count": entry.error_count,
            }
            for entry in stats.per_underlying
        ],
    }
    return json.dumps(payload, sort_keys=True)


def deserialize_websocket_statistics(payload: str) -> WebSocketStatistics:
    """Deserialize WebSocket statistics from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise KiteWebSocketError(
            f"Malformed JSON: {exc}",
            code="KITE_WS.SERIALIZATION.MALFORMED",
        ) from exc
    if data.get("schema_version") != KITE_WEBSOCKET_SCHEMA_VERSION:
        raise KiteWebSocketError(
            "Unsupported schema version.",
            code="KITE_WS.SERIALIZATION.UNSUPPORTED_VERSION",
        )
    per_underlying = tuple(
        UnderlyingStreamStats(
            underlying=str(entry["underlying"]),
            support_tier=UnderlyingSupportTier(entry["support_tier"]),
            desired_instrument_count=int(entry["desired_instrument_count"]),
            active_instrument_count=int(entry["active_instrument_count"]),
            tick_count=int(entry["tick_count"]),
            last_tick_at=(
                _parse_iso_datetime(entry["last_tick_at"])
                if entry.get("last_tick_at")
                else None
            ),
            seconds_since_last_tick=(
                float(entry["seconds_since_last_tick"])
                if entry.get("seconds_since_last_tick") is not None
                else None
            ),
            subscribe_success_count=int(entry["subscribe_success_count"]),
            subscribe_failure_count=int(entry["subscribe_failure_count"]),
            error_count=int(entry["error_count"]),
        )
        for entry in data.get("per_underlying", [])
    )
    return WebSocketStatistics(
        as_of=_parse_iso_datetime(data["as_of"]),
        connection_status=WebSocketConnectionStatus(data["connection_status"]),
        total_desired_instruments=int(data["total_desired_instruments"]),
        total_active_instruments=int(data["total_active_instruments"]),
        total_tick_count=int(data["total_tick_count"]),
        last_tick_at=(
            _parse_iso_datetime(data["last_tick_at"]) if data.get("last_tick_at") else None
        ),
        reconnect_count=int(data["reconnect_count"]),
        handler_error_count=int(data["handler_error_count"]),
        enabled_underlyings=tuple(data.get("enabled_underlyings", [])),
        per_underlying=per_underlying,
        unattributed_tick_count=int(data.get("unattributed_tick_count", 0)),
    )


def serialize_websocket_health_report(report: WebSocketHealthReport) -> str:
    """Serialize a health report to JSON (no secrets)."""
    payload = {
        "schema_version": KITE_WEBSOCKET_SCHEMA_VERSION,
        "report_id": report.report_id,
        "as_of": _isoformat_utc(report.as_of),
        "overall_health": report.overall_health.value,
        "connection_status": report.connection_status.value,
        "enabled_underlyings": list(report.enabled_underlyings),
        "healthy_underlyings": list(report.healthy_underlyings),
        "degraded_underlyings": list(report.degraded_underlyings),
        "unhealthy_underlyings": list(report.unhealthy_underlyings),
        "active_subscription_count": report.active_subscription_count,
        "issues": [
            {
                "issue_code": issue.issue_code,
                "severity": issue.severity,
                "message": issue.message,
                "underlying": issue.underlying,
                "instrument_token": issue.instrument_token,
            }
            for issue in report.issues
        ],
        "statistics": json.loads(serialize_websocket_statistics(report.statistics)),
    }
    return json.dumps(payload, sort_keys=True)


def deserialize_websocket_health_report(payload: str) -> WebSocketHealthReport:
    """Deserialize a health report from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise KiteWebSocketError(
            f"Malformed JSON: {exc}",
            code="KITE_WS.SERIALIZATION.MALFORMED",
        ) from exc
    if data.get("schema_version") != KITE_WEBSOCKET_SCHEMA_VERSION:
        raise KiteWebSocketError(
            "Unsupported schema version.",
            code="KITE_WS.SERIALIZATION.UNSUPPORTED_VERSION",
        )
    stats_payload = data.get("statistics", {})
    if isinstance(stats_payload, dict):
        stats = deserialize_websocket_statistics(json.dumps(stats_payload))
    else:
        stats = deserialize_websocket_statistics(str(stats_payload))
    issues = tuple(
        WebSocketHealthIssue(
            issue_code=str(issue["issue_code"]),
            severity=str(issue["severity"]),
            message=str(issue["message"]),
            underlying=issue.get("underlying"),
            instrument_token=issue.get("instrument_token"),
        )
        for issue in data.get("issues", [])
    )
    return WebSocketHealthReport(
        report_id=str(data["report_id"]),
        as_of=_parse_iso_datetime(data["as_of"]),
        overall_health=WebSocketHealthStatus(data["overall_health"]),
        connection_status=WebSocketConnectionStatus(data["connection_status"]),
        enabled_underlyings=tuple(data.get("enabled_underlyings", [])),
        healthy_underlyings=tuple(data.get("healthy_underlyings", [])),
        degraded_underlyings=tuple(data.get("degraded_underlyings", [])),
        unhealthy_underlyings=tuple(data.get("unhealthy_underlyings", [])),
        active_subscription_count=int(data["active_subscription_count"]),
        issues=issues,
        statistics=stats,
    )
