"""Secure Kite Connect authentication and session lifecycle for THETA AI TRADER.

Resolves credentials, exchanges request tokens for access tokens, persists and
restores sessions, detects expiry, and projects :class:`BrokerSession` objects
for :class:`~broker.zerodha.kite_broker.KiteBrokerClient`.

Never streams market data, places orders, evaluates strategies, calculates
risk, manages positions, or opens WebSockets.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Final, Mapping, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from broker.base_broker import BrokerId, BrokerSession
from config.application_configuration import (
    EnvironmentProfile,
    SecretProvider,
    SecretReference,
    SecretSource,
)

KITE_AUTHENTICATION_VERSION: Final[str] = "1.0.0"
KITE_AUTHENTICATION_SCHEMA_VERSION: Final[str] = "1.0.0"
TOKEN_ENVELOPE_SCHEMA_VERSION: Final[str] = "1.0.0"
PRODUCER_NAME: Final[str] = "kite_authentication"
DEFAULT_STORE_PATH: Final[str] = "data/auth/kite_session.json"
DEFAULT_CLOCK_SKEW_SECONDS: Final[float] = 30.0
IST_ZONE: Final[str] = "Asia/Kolkata"
_IST: Final[ZoneInfo] = ZoneInfo(IST_ZONE)

_PLACEHOLDER_CREDENTIALS: Final[frozenset[str]] = frozenset(
    {
        "change_me",
        "your_api_key",
        "your_api_secret",
        "your_access_token",
        "xxx",
        "TODO",
        "todo",
    }
)

_TOKEN_LIKE = re.compile(r"[A-Za-z0-9]{8,}")
_LOGGER = logging.getLogger("broker.kite_authentication")

SdkFactory = Callable[[str], Any]


class AuthenticationStatus(str, Enum):
    """Lifecycle status of the authenticator."""

    UNAUTHENTICATED = "unauthenticated"
    CREDENTIALS_LOADED = "credentials_loaded"
    AWAITING_REQUEST_TOKEN = "awaiting_request_token"
    EXCHANGING = "exchanging"
    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"
    REVOKED = "revoked"
    LOGGED_OUT = "logged_out"
    FAILED = "failed"
    DEGRADED = "degraded"


class AuthHealthStatus(str, Enum):
    """Aggregated authentication health band."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class CredentialSource(str, Enum):
    """How API credentials were resolved."""

    EXPLICIT = "explicit"
    ENVIRONMENT = "environment"
    SECRET_PROVIDER = "secret_provider"
    MIXED = "mixed"
    UNAVAILABLE = "unavailable"


class TokenSource(str, Enum):
    """How the access token was obtained."""

    NONE = "none"
    EXCHANGE = "exchange"
    RESTORED = "restored"
    INJECTED = "injected"


class TokenPersistenceMode(str, Enum):
    """Persistence backend selector."""

    DISABLED = "disabled"
    FILE = "file"
    ENV_FILE = "env_file"
    CUSTOM = "custom"


class ExpiryPolicyKind(str, Enum):
    """How expiry hints are assigned when the SDK omits them."""

    NONE = "none"
    NEXT_0600_IST = "next_0600_ist"
    FIXED_HOURS = "fixed_hours"
    EXPLICIT = "explicit"


class AuthenticationError(Exception):
    """Base exception for Kite authentication failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        field: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.correlation_id = correlation_id


class InvalidCredentialError(AuthenticationError):
    """Raised when credentials fail local validation."""


class SessionExpiredError(AuthenticationError):
    """Raised when a session is expired or vendor rejects the token."""


class TokenPersistenceError(AuthenticationError):
    """Raised when token persistence or restoration fails."""


@dataclass(frozen=True)
class AuthenticationWarningRecord:
    """Non-fatal authentication warning."""

    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True)
class AuthenticationErrorRecord:
    """Structured authentication error record."""

    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True)
class AuthenticationHealthIssue:
    """Structured health issue for authentication preflight."""

    issue_code: str
    severity: str
    message: str
    field: str | None = None


@dataclass(frozen=True)
class KiteAuthenticationConfig:
    """Immutable authentication policy and environment settings."""

    environment_profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT
    api_key_env: str = "THETA_BROKER_API_KEY"
    api_secret_env: str = "THETA_BROKER_API_SECRET"
    access_token_env: str = "THETA_BROKER_ACCESS_TOKEN"
    legacy_api_key_env: str = "KITE_API_KEY"
    legacy_api_secret_env: str = "KITE_API_SECRET"
    legacy_access_token_env: str = "KITE_ACCESS_TOKEN"
    persistence_mode: TokenPersistenceMode = TokenPersistenceMode.FILE
    token_store_path: str = DEFAULT_STORE_PATH
    env_file_path: str = ".env"
    require_profile_probe: bool = False
    fail_closed_on_expiry: bool = True
    clock_skew_seconds: float = DEFAULT_CLOCK_SKEW_SECONDS
    default_expiry_policy: ExpiryPolicyKind = ExpiryPolicyKind.NEXT_0600_IST
    fixed_ttl_hours: float = 18.0
    allow_missing_secret_in_restore: bool = True
    allow_env_file_persistence: bool = True
    max_request_token_age_seconds: float = 300.0
    min_credential_length: int = 6
    deterministic_fingerprint: bool = True
    publish_auth_events: bool = False
    runner_kind: str = "cli"
    auto_clear_corrupt_store: bool = False
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.clock_skew_seconds < 0:
            raise AuthenticationError(
                "clock_skew_seconds must be >= 0.",
                code="KITE_AUTH.CONFIG.INVALID",
                field="clock_skew_seconds",
            )
        if (
            self.environment_profile is EnvironmentProfile.PRODUCTION
            and self.persistence_mode is TokenPersistenceMode.ENV_FILE
            and not self.allow_env_file_persistence
        ):
            raise TokenPersistenceError(
                "ENV_FILE persistence is forbidden in production.",
                code="KITE_AUTH.PERSIST.MODE_FORBIDDEN",
                field="persistence_mode",
            )


@dataclass(frozen=True)
class SessionMetadata:
    """Audit and diagnostic metadata for an authentication operation."""

    environment_profile: EnvironmentProfile
    runner_kind: str
    credential_source: CredentialSource
    token_source: TokenSource
    persistence_backend: str | None = None
    login_url_issued: bool = False
    profile_probe_performed: bool = False
    clock_skew_seconds: float = DEFAULT_CLOCK_SKEW_SECONDS
    session_fingerprint: str | None = None
    audit_tags: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class KiteSession:
    """Immutable authenticated Kite session artifact."""

    session_id: str
    broker_id: BrokerId
    api_key: str
    access_token: str
    authenticated_at: datetime
    session_fingerprint: str
    environment_profile: EnvironmentProfile
    user_id: str | None = None
    user_name: str | None = None
    expires_at: datetime | None = None
    login_time: datetime | None = None
    exchange_metadata: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __repr__(self) -> str:
        return (
            f"KiteSession(session_id={self.session_id!r}, broker_id={self.broker_id!r}, "
            f"api_key={redact_secret(self.api_key)!r}, access_token={redact_secret(self.access_token)!r}, "
            f"user_id={self.user_id!r}, authenticated_at={self.authenticated_at!r}, "
            f"expires_at={self.expires_at!r}, session_fingerprint={self.session_fingerprint!r})"
        )

    def to_broker_session(self) -> BrokerSession:
        """Project this session into a broker-neutral :class:`BrokerSession`."""
        return project_broker_session(self)


@dataclass(frozen=True)
class AuthenticationResult:
    """Immutable outcome of an authenticate, restore, inject, or logout call."""

    result_id: str
    status: AuthenticationStatus
    session: KiteSession | None
    broker_session: BrokerSession | None
    metadata: SessionMetadata
    warnings: tuple[AuthenticationWarningRecord, ...]
    errors: tuple[AuthenticationErrorRecord, ...]
    started_at: datetime
    completed_at: datetime
    duration_ms: float
    correlation_id: str


@dataclass(frozen=True)
class AuthenticationHealthReport:
    """Aggregated authentication health snapshot without secret values."""

    report_id: str
    as_of: datetime
    status: AuthenticationStatus
    overall_health: AuthHealthStatus
    has_api_key: bool
    has_api_secret: bool
    has_access_token: bool
    session_id: str | None
    expires_at: datetime | None
    seconds_to_expiry: float | None
    is_expired: bool
    persistence_available: bool
    last_error_code: str | None
    last_error_message: str | None
    session_fingerprint: str | None
    issues: tuple[AuthenticationHealthIssue, ...]
    last_validated_at: datetime | None = None
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class TokenEnvelope:
    """Persisted token representation — never includes api_secret."""

    schema_version: str
    session_id: str
    api_key: str
    access_token: str
    authenticated_at: datetime
    session_fingerprint: str
    environment_profile: str
    checksum: str
    user_id: str | None = None
    user_name: str | None = None
    expires_at: datetime | None = None


@runtime_checkable
class TokenStore(Protocol):
    """Persistence backend for token envelopes."""

    def save(self, envelope: TokenEnvelope) -> None:
        """Persist an envelope atomically."""

    def load(self) -> TokenEnvelope | None:
        """Load the current envelope, or ``None`` when absent."""

    def clear(self) -> None:
        """Remove persisted session material."""

    def is_available(self) -> bool:
        """Return whether the store is reachable."""


def _utc_now() -> datetime:
    """Return the current UTC timestamp with timezone information."""
    return datetime.now(timezone.utc)


def _is_timezone_aware(value: datetime) -> bool:
    """Return whether a datetime carries timezone information."""
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _isoformat_utc(value: datetime) -> str:
    """Serialize a timezone-aware datetime as ISO-8601 UTC with ``Z``."""
    if not _is_timezone_aware(value):
        raise AuthenticationError(
            "Datetime must be timezone-aware.",
            code="KITE_AUTH.SESSION.NAIVE_TIMESTAMP",
        )
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


def _canonical_json(payload: Mapping[str, object]) -> str:
    """Return deterministic JSON for fingerprints and checksums."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def redact_secret(value: str | None) -> str:
    """Return a stable redaction marker for logs and errors.

    Args:
        value: Secret or token value, if any.

    Returns:
        ``"<missing>"`` when empty, otherwise ``"<redacted>"``.
    """
    if not value:
        return "<missing>"
    return "<redacted>"


def _redact_message(message: str) -> str:
    """Redact token-like substrings from vendor or exception messages."""
    return _TOKEN_LIKE.sub("<redacted>", message)


def default_kite_authentication_config(
    profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT,
) -> KiteAuthenticationConfig:
    """Return profile-aware default authentication configuration.

    Args:
        profile: Target environment profile.

    Returns:
        Immutable :class:`KiteAuthenticationConfig`.
    """
    if profile is EnvironmentProfile.PRODUCTION:
        return KiteAuthenticationConfig(
            environment_profile=profile,
            persistence_mode=TokenPersistenceMode.FILE,
            require_profile_probe=True,
            fail_closed_on_expiry=True,
            allow_env_file_persistence=False,
            runner_kind="live_trading",
        )
    if profile is EnvironmentProfile.PAPER:
        return KiteAuthenticationConfig(
            environment_profile=profile,
            persistence_mode=TokenPersistenceMode.FILE,
            require_profile_probe=True,
            fail_closed_on_expiry=True,
            allow_env_file_persistence=False,
            runner_kind="paper_trading",
        )
    return KiteAuthenticationConfig(
        environment_profile=profile,
        persistence_mode=TokenPersistenceMode.FILE,
        require_profile_probe=False,
        allow_env_file_persistence=True,
        runner_kind="cli",
    )


def validate_kite_credentials(
    api_key: str | None,
    api_secret: str | None,
    *,
    require_secret: bool = True,
    min_length: int = 6,
    reject_placeholders: bool = True,
) -> None:
    """Validate API key and optional API secret locally.

    Args:
        api_key: Kite API key.
        api_secret: Kite API secret.
        require_secret: Whether api_secret is required.
        min_length: Minimum length for non-empty credentials.
        reject_placeholders: Whether placeholder values are rejected.

    Raises:
        InvalidCredentialError: When validation fails.
    """
    if api_key is None or not str(api_key).strip():
        raise InvalidCredentialError(
            "API key is required.",
            code="KITE_AUTH.CREDENTIAL.MISSING_API_KEY",
            field="api_key",
        )
    key = str(api_key).strip()
    if len(key) < min_length:
        raise InvalidCredentialError(
            "API key failed validation.",
            code="KITE_AUTH.CREDENTIAL.INVALID_API_KEY",
            field="api_key",
        )
    if reject_placeholders and key.lower() in {p.lower() for p in _PLACEHOLDER_CREDENTIALS}:
        raise InvalidCredentialError(
            "Placeholder API key rejected.",
            code="KITE_AUTH.CREDENTIAL.PLACEHOLDER",
            field="api_key",
        )
    if require_secret:
        if api_secret is None or not str(api_secret).strip():
            raise InvalidCredentialError(
                "API secret is required.",
                code="KITE_AUTH.CREDENTIAL.MISSING_API_SECRET",
                field="api_secret",
            )
        secret = str(api_secret).strip()
        if len(secret) < min_length:
            raise InvalidCredentialError(
                "API secret failed validation.",
                code="KITE_AUTH.CREDENTIAL.INVALID_API_SECRET",
                field="api_secret",
            )
        if reject_placeholders and secret.lower() in {
            p.lower() for p in _PLACEHOLDER_CREDENTIALS
        }:
            raise InvalidCredentialError(
                "Placeholder API secret rejected.",
                code="KITE_AUTH.CREDENTIAL.PLACEHOLDER",
                field="api_secret",
            )


def validate_kite_session(session: KiteSession) -> KiteSession:
    """Validate a sealed :class:`KiteSession`.

    Args:
        session: Session to validate.

    Returns:
        The same session when valid.

    Raises:
        AuthenticationError: When session invariants fail.
        InvalidCredentialError: When credentials are invalid.
    """
    if not session.session_id or not session.session_id.strip():
        raise AuthenticationError(
            "session_id must be non-empty.",
            code="KITE_AUTH.SESSION.INVALID_ID",
            field="session_id",
        )
    if session.broker_id is not BrokerId.KITE:
        raise AuthenticationError(
            "broker_id must be KITE.",
            code="KITE_AUTH.SESSION.INVALID_BROKER",
            field="broker_id",
        )
    if not _is_timezone_aware(session.authenticated_at):
        raise AuthenticationError(
            "authenticated_at must be timezone-aware.",
            code="KITE_AUTH.SESSION.NAIVE_TIMESTAMP",
            field="authenticated_at",
        )
    if session.expires_at is not None and not _is_timezone_aware(session.expires_at):
        raise AuthenticationError(
            "expires_at must be timezone-aware when provided.",
            code="KITE_AUTH.SESSION.NAIVE_TIMESTAMP",
            field="expires_at",
        )
    if not session.api_key.strip() or not session.access_token.strip():
        raise InvalidCredentialError(
            "Session credentials are invalid.",
            code="KITE_AUTH.SESSION.INVALID_CREDENTIALS",
        )
    return session


def compute_session_fingerprint(
    *,
    session_id: str,
    api_key: str,
    access_token: str,
    authenticated_at: datetime,
    expires_at: datetime | None,
    user_id: str | None,
    environment_profile: str,
) -> str:
    """Compute deterministic SHA-256 session fingerprint.

    Args:
        session_id: Session correlation id.
        api_key: API key.
        access_token: Access token (hashed, never stored raw in fingerprint).
        authenticated_at: Issuance timestamp.
        expires_at: Optional expiry hint.
        user_id: Optional user id.
        environment_profile: Profile string value.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    payload = {
        "session_id": session_id,
        "api_key": api_key,
        "access_token_sha256": hashlib.sha256(access_token.encode("utf-8")).hexdigest(),
        "authenticated_at": _isoformat_utc(authenticated_at),
        "expires_at": _isoformat_utc(expires_at) if expires_at else None,
        "user_id": user_id,
        "environment_profile": environment_profile,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_next_0600_ist(authenticated_at: datetime) -> datetime:
    """Compute the next 06:00 Asia/Kolkata boundary after authentication.

    Args:
        authenticated_at: Timezone-aware authentication timestamp.

    Returns:
        Timezone-aware expiry hint.
    """
    if not _is_timezone_aware(authenticated_at):
        raise AuthenticationError(
            "authenticated_at must be timezone-aware.",
            code="KITE_AUTH.SESSION.NAIVE_TIMESTAMP",
            field="authenticated_at",
        )
    local = authenticated_at.astimezone(_IST)
    candidate = local.replace(hour=6, minute=0, second=0, microsecond=0)
    if local >= candidate:
        candidate = candidate + timedelta(days=1)
    return candidate


def is_session_expired(
    expires_at: datetime | None,
    *,
    now: datetime,
    skew_seconds: float,
) -> bool:
    """Return whether a session is expired under clock-skew policy.

    Args:
        expires_at: Optional expiry hint.
        now: Current timezone-aware clock.
        skew_seconds: Non-negative skew allowance.

    Returns:
        ``True`` when ``expires_at`` is known and ``now`` is at/after expiry-skew.
    """
    if expires_at is None:
        return False
    if skew_seconds < 0:
        raise AuthenticationError(
            "skew_seconds must be >= 0.",
            code="KITE_AUTH.CONFIG.INVALID",
            field="clock_skew_seconds",
        )
    return now >= (expires_at - timedelta(seconds=skew_seconds))


def project_broker_session(session: KiteSession) -> BrokerSession:
    """Project a :class:`KiteSession` into a :class:`BrokerSession`.

    Args:
        session: Sealed Kite session.

    Returns:
        Broker-neutral session for :class:`KiteBrokerClient`.
    """
    validate_kite_session(session)
    return BrokerSession(
        broker_id=BrokerId.KITE,
        session_id=session.session_id,
        authenticated_at=session.authenticated_at,
        expires_at=session.expires_at,
        credentials=MappingProxyType(
            {
                "api_key": session.api_key,
                "access_token": session.access_token,
            }
        ),
    )


def _envelope_checksum_payload(envelope: TokenEnvelope) -> Mapping[str, object]:
    """Build checksum input excluding the checksum field itself."""
    return {
        "schema_version": envelope.schema_version,
        "session_id": envelope.session_id,
        "api_key": envelope.api_key,
        "access_token": envelope.access_token,
        "user_id": envelope.user_id,
        "user_name": envelope.user_name,
        "authenticated_at": _isoformat_utc(envelope.authenticated_at),
        "expires_at": _isoformat_utc(envelope.expires_at) if envelope.expires_at else None,
        "session_fingerprint": envelope.session_fingerprint,
        "environment_profile": envelope.environment_profile,
    }


def compute_envelope_checksum(envelope: TokenEnvelope) -> str:
    """Compute integrity checksum for a token envelope."""
    return hashlib.sha256(
        _canonical_json(_envelope_checksum_payload(envelope)).encode("utf-8")
    ).hexdigest()


class NullTokenStore:
    """No-op token store for ephemeral / test sessions."""

    def save(self, envelope: TokenEnvelope) -> None:
        """Ignore persistence requests."""
        del envelope

    def load(self) -> TokenEnvelope | None:
        """Return ``None`` — nothing is stored."""
        return None

    def clear(self) -> None:
        """No-op clear."""

    def is_available(self) -> bool:
        """Return ``True`` — null store is always available."""
        return True


class FileTokenStore:
    """POSIX file-backed token store with atomic replace and ``0600`` mode."""

    def __init__(self, path: str | Path) -> None:
        """Initialize file store.

        Args:
            path: Destination JSON path.
        """
        self._path = Path(path)
        self._lock = threading.RLock()

    def save(self, envelope: TokenEnvelope) -> None:
        """Persist envelope via temp file and atomic replace."""
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(self._path.parent, 0o700)
                except OSError:
                    pass
                payload = {
                    "schema_version": envelope.schema_version,
                    "session_id": envelope.session_id,
                    "api_key": envelope.api_key,
                    "access_token": envelope.access_token,
                    "user_id": envelope.user_id,
                    "user_name": envelope.user_name,
                    "authenticated_at": _isoformat_utc(envelope.authenticated_at),
                    "expires_at": (
                        _isoformat_utc(envelope.expires_at) if envelope.expires_at else None
                    ),
                    "session_fingerprint": envelope.session_fingerprint,
                    "environment_profile": envelope.environment_profile,
                    "checksum": envelope.checksum,
                }
                tmp = self._path.with_suffix(self._path.suffix + ".tmp")
                tmp.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
                os.chmod(tmp, 0o600)
                os.replace(tmp, self._path)
                os.chmod(self._path, 0o600)
            except OSError as exc:
                raise TokenPersistenceError(
                    f"Failed to persist token envelope: {_redact_message(str(exc))}",
                    code="KITE_AUTH.PERSIST.IO_ERROR",
                ) from exc

    def load(self) -> TokenEnvelope | None:
        """Load and validate envelope from disk."""
        with self._lock:
            if not self._path.is_file():
                return None
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise TokenPersistenceError(
                    f"Failed to load token envelope: {_redact_message(str(exc))}",
                    code="KITE_AUTH.PERSIST.IO_ERROR",
                ) from exc
            return _deserialize_token_envelope(raw)

    def clear(self) -> None:
        """Delete the envelope file when present."""
        with self._lock:
            try:
                if self._path.is_file():
                    self._path.unlink()
            except OSError as exc:
                raise TokenPersistenceError(
                    f"Failed to clear token store: {_redact_message(str(exc))}",
                    code="KITE_AUTH.PERSIST.IO_ERROR",
                ) from exc

    def is_available(self) -> bool:
        """Return whether the parent directory is usable."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False


class EnvFileTokenStore:
    """Development convenience store that writes access token into a ``.env`` file."""

    def __init__(
        self,
        path: str | Path = ".env",
        *,
        access_token_key: str = "THETA_BROKER_ACCESS_TOKEN",
        api_key_key: str = "THETA_BROKER_API_KEY",
    ) -> None:
        """Initialize env-file store.

        Args:
            path: Target ``.env`` path.
            access_token_key: Env key for access token.
            api_key_key: Env key for API key metadata companion.
        """
        self._path = Path(path)
        self._access_token_key = access_token_key
        self._api_key_key = api_key_key
        self._lock = threading.RLock()
        self._memory: TokenEnvelope | None = None

    def save(self, envelope: TokenEnvelope) -> None:
        """Update env file keys without printing secret values."""
        with self._lock:
            try:
                lines: list[str] = []
                if self._path.is_file():
                    lines = self._path.read_text(encoding="utf-8").splitlines()
                updated = {
                    self._access_token_key: envelope.access_token,
                    self._api_key_key: envelope.api_key,
                }
                seen: set[str] = set()
                new_lines: list[str] = []
                for line in lines:
                    if "=" not in line or line.strip().startswith("#"):
                        new_lines.append(line)
                        continue
                    key, _sep, _value = line.partition("=")
                    key = key.strip()
                    if key in updated:
                        new_lines.append(f"{key}={updated[key]}")
                        seen.add(key)
                    else:
                        new_lines.append(line)
                for key, value in updated.items():
                    if key not in seen:
                        new_lines.append(f"{key}={value}")
                self._path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                try:
                    os.chmod(self._path, 0o600)
                except OSError:
                    pass
                self._memory = envelope
            except OSError as exc:
                raise TokenPersistenceError(
                    f"Failed to write env file: {_redact_message(str(exc))}",
                    code="KITE_AUTH.PERSIST.IO_ERROR",
                ) from exc

    def load(self) -> TokenEnvelope | None:
        """Return last in-memory envelope when available."""
        with self._lock:
            return self._memory

    def clear(self) -> None:
        """Clear in-memory envelope; does not rewrite entire env file."""
        with self._lock:
            self._memory = None

    def is_available(self) -> bool:
        """Return ``True`` when path parent is writable."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False


def _deserialize_token_envelope(raw: Mapping[str, Any]) -> TokenEnvelope:
    """Deserialize and verify a token envelope mapping."""
    schema = str(raw.get("schema_version", ""))
    if schema != TOKEN_ENVELOPE_SCHEMA_VERSION:
        raise TokenPersistenceError(
            "Unsupported token envelope schema version.",
            code="KITE_AUTH.PERSIST.UNSUPPORTED_VERSION",
        )
    envelope = TokenEnvelope(
        schema_version=schema,
        session_id=str(raw["session_id"]),
        api_key=str(raw["api_key"]),
        access_token=str(raw["access_token"]),
        user_id=str(raw["user_id"]) if raw.get("user_id") else None,
        user_name=str(raw["user_name"]) if raw.get("user_name") else None,
        authenticated_at=_parse_iso_datetime(str(raw["authenticated_at"])),
        expires_at=(
            _parse_iso_datetime(str(raw["expires_at"])) if raw.get("expires_at") else None
        ),
        session_fingerprint=str(raw["session_fingerprint"]),
        environment_profile=str(raw["environment_profile"]),
        checksum=str(raw.get("checksum", "")),
    )
    expected = compute_envelope_checksum(envelope)
    if envelope.checksum != expected:
        raise TokenPersistenceError(
            "Token envelope checksum mismatch.",
            code="KITE_AUTH.PERSIST.CHECKSUM_MISMATCH",
        )
    return envelope


def _default_sdk_factory(api_key: str) -> Any:
    """Construct the official KiteConnect client for auth-only methods."""
    try:
        from kiteconnect import KiteConnect
    except ImportError as exc:
        raise AuthenticationError(
            f"kiteconnect SDK is unavailable: {exc}",
            code="KITE_AUTH.EXCHANGE.SDK_UNAVAILABLE",
        ) from exc
    return KiteConnect(api_key=api_key)


@dataclass(frozen=True)
class _ResolvedCredentials:
    """Internal credential snapshot."""

    api_key: str | None
    api_secret: str | None
    access_token: str | None
    source: CredentialSource


class KiteAuthenticator:
    """Secure Kite Connect authentication and session lifecycle service.

    Resolves credentials, exchanges request tokens for access tokens,
    persists and restores sessions, detects expiry, and projects
    BrokerSession objects for KiteBrokerClient. Never streams market
    data, places orders, or opens WebSockets.

    Args:
        config: Authentication policy and environment settings.
        secret_provider: Optional SecretProvider for credential resolution.
        token_store: Optional TokenStore override.
        clock: Injectable clock for deterministic timestamps.
        sdk_factory: Injectable KiteConnect factory for tests.
        env: Optional environment mapping; defaults to ``os.environ``.
        api_key: Optional explicit API key.
        api_secret: Optional explicit API secret.
        access_token: Optional explicit access token for restore/inject bootstrap.
        secret_refs: Optional mapping of logical secret names to references.
    """

    def __init__(
        self,
        config: KiteAuthenticationConfig | None = None,
        *,
        secret_provider: SecretProvider | None = None,
        token_store: TokenStore | None = None,
        clock: Callable[[], datetime] | None = None,
        sdk_factory: SdkFactory | None = None,
        env: Mapping[str, str] | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        access_token: str | None = None,
        secret_refs: Mapping[str, SecretReference] | None = None,
    ) -> None:
        self._config = config or default_kite_authentication_config()
        self._secret_provider = secret_provider
        self._secret_refs = dict(secret_refs or {})
        self._clock = clock or _utc_now
        self._sdk_factory = sdk_factory or _default_sdk_factory
        self._env = dict(env) if env is not None else dict(os.environ)
        self._explicit_api_key = api_key
        self._explicit_api_secret = api_secret
        self._explicit_access_token = access_token
        self._token_store = token_store or self._build_default_store()
        self._lock = threading.RLock()
        self._status = AuthenticationStatus.UNAUTHENTICATED
        self._session: KiteSession | None = None
        self._api_key: str | None = None
        self._api_secret: str | None = None
        self._login_url_issued = False
        self._last_error_code: str | None = None
        self._last_error_message: str | None = None
        self._last_validated_at: datetime | None = None
        self._credential_source = CredentialSource.UNAVAILABLE
        resolved = self._resolve_credentials()
        self._api_key = resolved.api_key
        self._api_secret = resolved.api_secret
        self._credential_source = resolved.source
        if self._api_key:
            try:
                reject = self._config.environment_profile is not EnvironmentProfile.DEVELOPMENT
                validate_kite_credentials(
                    self._api_key,
                    self._api_secret,
                    require_secret=False,
                    min_length=self._config.min_credential_length,
                    reject_placeholders=reject or bool(self._api_secret),
                )
                self._status = AuthenticationStatus.CREDENTIALS_LOADED
            except InvalidCredentialError as exc:
                self._last_error_code = exc.code
                self._last_error_message = str(exc)
                if self._config.environment_profile is EnvironmentProfile.PRODUCTION:
                    raise

    def _build_default_store(self) -> TokenStore:
        mode = self._config.persistence_mode
        if mode is TokenPersistenceMode.DISABLED:
            return NullTokenStore()
        if mode is TokenPersistenceMode.ENV_FILE:
            if (
                self._config.environment_profile is EnvironmentProfile.PRODUCTION
                and not self._config.allow_env_file_persistence
            ):
                raise TokenPersistenceError(
                    "ENV_FILE persistence is forbidden in production.",
                    code="KITE_AUTH.PERSIST.MODE_FORBIDDEN",
                )
            return EnvFileTokenStore(
                self._config.env_file_path,
                access_token_key=self._config.access_token_env,
                api_key_key=self._config.api_key_env,
            )
        if mode is TokenPersistenceMode.CUSTOM:
            raise AuthenticationError(
                "CUSTOM persistence requires an injected TokenStore.",
                code="KITE_AUTH.CONFIG.INVALID",
                field="persistence_mode",
            )
        return FileTokenStore(self._config.token_store_path)

    def _resolve_credentials(self) -> _ResolvedCredentials:
        sources: set[CredentialSource] = set()
        api_key = self._explicit_api_key
        api_secret = self._explicit_api_secret
        access_token = self._explicit_access_token
        if api_key or api_secret or access_token:
            sources.add(CredentialSource.EXPLICIT)

        if self._secret_provider is not None:
            resolved_map = {
                "broker.api_key": api_key,
                "broker.api_secret": api_secret,
                "broker.access_token": access_token,
            }
            for name in ("broker.api_key", "broker.api_secret", "broker.access_token"):
                if resolved_map[name]:
                    continue
                ref = self._secret_refs.get(name)
                if ref is None:
                    continue
                if not self._secret_provider.is_available(ref):
                    if ref.required:
                        raise InvalidCredentialError(
                            f"Secret {name!r} is not available.",
                            code="KITE_AUTH.CREDENTIAL.SECRET_UNRESOLVED",
                            field=name,
                        )
                    continue
                value = self._secret_provider.get_secret(ref)
                resolved_map[name] = value
                sources.add(CredentialSource.SECRET_PROVIDER)
            api_key = resolved_map["broker.api_key"]
            api_secret = resolved_map["broker.api_secret"]
            access_token = resolved_map["broker.access_token"]

        def _env_get(*keys: str) -> str | None:
            for key in keys:
                value = self._env.get(key, "").strip()
                if value:
                    return value
            return None

        allow_legacy = self._config.environment_profile is not EnvironmentProfile.PRODUCTION
        if not api_key:
            keys = [self._config.api_key_env]
            if allow_legacy:
                keys.append(self._config.legacy_api_key_env)
            api_key = _env_get(*keys)
            if api_key:
                sources.add(CredentialSource.ENVIRONMENT)
        if not api_secret:
            keys = [self._config.api_secret_env]
            if allow_legacy:
                keys.append(self._config.legacy_api_secret_env)
            api_secret = _env_get(*keys)
            if api_secret:
                sources.add(CredentialSource.ENVIRONMENT)
        if not access_token:
            keys = [self._config.access_token_env]
            if allow_legacy:
                keys.append(self._config.legacy_access_token_env)
            access_token = _env_get(*keys)
            if access_token:
                sources.add(CredentialSource.ENVIRONMENT)

        if not sources:
            source = CredentialSource.UNAVAILABLE
        elif len(sources) == 1:
            source = next(iter(sources))
        else:
            source = CredentialSource.MIXED
        self._explicit_access_token = access_token
        return _ResolvedCredentials(
            api_key=api_key,
            api_secret=api_secret,
            access_token=access_token,
            source=source,
        )

    @property
    def config(self) -> KiteAuthenticationConfig:
        """Return the immutable authentication configuration."""
        return self._config

    def get_status(self) -> AuthenticationStatus:
        """Return current authenticator status."""
        with self._lock:
            return self._status

    def get_session(self) -> KiteSession | None:
        """Return the current sealed session, if any."""
        with self._lock:
            return self._session

    def get_login_url(self) -> str:
        """Build the Kite Connect login URL for interactive operator flows.

        Returns:
            Login URL string.

        Raises:
            InvalidCredentialError: When API key is missing/invalid.
            AuthenticationError: When SDK construction fails.
        """
        with self._lock:
            api_key = self._api_key
        if not api_key:
            raise InvalidCredentialError(
                "API key is required to build login URL.",
                code="KITE_AUTH.CREDENTIAL.MISSING_API_KEY",
                field="api_key",
            )
        validate_kite_credentials(
            api_key,
            None,
            require_secret=False,
            min_length=self._config.min_credential_length,
            reject_placeholders=self._config.environment_profile
            is EnvironmentProfile.PRODUCTION,
        )
        client = self._sdk_factory(api_key)
        url = str(client.login_url())
        with self._lock:
            self._status = AuthenticationStatus.AWAITING_REQUEST_TOKEN
            self._login_url_issued = True
        _LOGGER.info("kite_auth.login_url.issued")
        return url

    def authenticate(
        self,
        request_token: str,
        *,
        correlation_id: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> AuthenticationResult:
        """Exchange ``request_token`` for an access token and seal a session.

        Args:
            request_token: One-time token from Kite login redirect.
            correlation_id: Optional correlation id.
            metadata: Optional audit tags.

        Returns:
            Structured authentication result.
        """
        started = self._clock()
        start_perf = time.perf_counter()
        corr = correlation_id or str(uuid.uuid4())
        warnings: list[AuthenticationWarningRecord] = []
        errors: list[AuthenticationErrorRecord] = []
        probe_performed = False
        token_source = TokenSource.EXCHANGE

        try:
            with self._lock:
                self._status = AuthenticationStatus.EXCHANGING
                api_key = self._api_key
                api_secret = self._api_secret

            if not request_token or not request_token.strip():
                raise InvalidCredentialError(
                    "request_token is required.",
                    code="KITE_AUTH.CREDENTIAL.MISSING_REQUEST_TOKEN",
                    field="request_token",
                )
            reject = self._config.environment_profile is not EnvironmentProfile.DEVELOPMENT
            validate_kite_credentials(
                api_key,
                api_secret,
                require_secret=True,
                min_length=self._config.min_credential_length,
                reject_placeholders=reject,
            )
            assert api_key is not None and api_secret is not None

            client = self._sdk_factory(api_key)
            try:
                data = client.generate_session(
                    request_token.strip(),
                    api_secret=api_secret,
                )
            except AuthenticationError:
                raise
            except Exception as exc:
                raise AuthenticationError(
                    f"Token exchange failed: {_redact_message(str(exc))}",
                    code="KITE_AUTH.EXCHANGE.FAILED",
                    correlation_id=corr,
                ) from exc

            if not isinstance(data, Mapping):
                raise AuthenticationError(
                    "Malformed exchange response.",
                    code="KITE_AUTH.EXCHANGE.MALFORMED_RESPONSE",
                    correlation_id=corr,
                )
            access_token = data.get("access_token")
            if not isinstance(access_token, str) or not access_token.strip():
                raise AuthenticationError(
                    "Exchange response missing access_token.",
                    code="KITE_AUTH.EXCHANGE.MALFORMED_RESPONSE",
                    correlation_id=corr,
                )
            user_id = str(data["user_id"]) if data.get("user_id") else None
            user_name = str(data["user_name"]) if data.get("user_name") else None
            exchange_meta: dict[str, str] = {}
            if data.get("broker"):
                exchange_meta["broker"] = str(data["broker"])

            if self._config.require_profile_probe:
                probe_performed = True
                user_id, user_name, probe_warnings = self._run_profile_probe(
                    client,
                    access_token.strip(),
                    user_id=user_id,
                    user_name=user_name,
                    required=True,
                )
                warnings.extend(probe_warnings)
            elif hasattr(client, "set_access_token"):
                try:
                    client.set_access_token(access_token.strip())
                except Exception:
                    pass

            authenticated_at = started if _is_timezone_aware(started) else _utc_now()
            expires_at = self._assign_expiry(authenticated_at, explicit=None)
            session = self._seal_session(
                api_key=api_key,
                access_token=access_token.strip(),
                authenticated_at=authenticated_at,
                expires_at=expires_at,
                user_id=user_id,
                user_name=user_name,
                exchange_metadata=exchange_meta,
                metadata=metadata,
            )
            self._persist_session(session)
            status = AuthenticationStatus.AUTHENTICATED
            if expires_at is None and self._config.environment_profile is EnvironmentProfile.PRODUCTION:
                status = AuthenticationStatus.DEGRADED
                warnings.append(
                    AuthenticationWarningRecord(
                        code="KITE_AUTH.HEALTH.TOKEN_EXPIRING_SOON",
                        message="Production session sealed without expiry hint.",
                    )
                )
            broker_session = project_broker_session(session)
            with self._lock:
                self._session = session
                self._status = status
                self._last_error_code = None
                self._last_error_message = None
            completed = self._clock()
            result = AuthenticationResult(
                result_id=str(uuid.uuid4()),
                status=status,
                session=session,
                broker_session=broker_session,
                metadata=self._build_metadata(
                    token_source=token_source,
                    fingerprint=session.session_fingerprint,
                    probe_performed=probe_performed,
                    audit_tags=metadata,
                ),
                warnings=tuple(warnings),
                errors=tuple(errors),
                started_at=started,
                completed_at=completed,
                duration_ms=(time.perf_counter() - start_perf) * 1000,
                correlation_id=corr,
            )
            _LOGGER.info("kite_auth.exchange.success")
            return result
        except AuthenticationError as exc:
            return self._failure_result(
                started=started,
                start_perf=start_perf,
                corr=corr,
                exc=exc,
                token_source=token_source,
                warnings=warnings,
                metadata=metadata,
                probe_performed=probe_performed,
            )

    def restore_session(
        self,
        *,
        correlation_id: str | None = None,
    ) -> AuthenticationResult:
        """Restore a persisted session from the configured token store.

        Args:
            correlation_id: Optional correlation id.

        Returns:
            Structured authentication result.
        """
        started = self._clock()
        start_perf = time.perf_counter()
        corr = correlation_id or str(uuid.uuid4())
        warnings: list[AuthenticationWarningRecord] = []
        probe_performed = False
        try:
            envelope = self._token_store.load()
            if envelope is None and self._explicit_access_token and self._api_key:
                return self.inject_access_token(
                    self._explicit_access_token,
                    api_key=self._api_key,
                    correlation_id=corr,
                )
            if envelope is None:
                with self._lock:
                    self._status = AuthenticationStatus.UNAUTHENTICATED
                    self._session = None
                completed = self._clock()
                return AuthenticationResult(
                    result_id=str(uuid.uuid4()),
                    status=AuthenticationStatus.UNAUTHENTICATED,
                    session=None,
                    broker_session=None,
                    metadata=self._build_metadata(
                        token_source=TokenSource.NONE,
                        fingerprint=None,
                        probe_performed=False,
                    ),
                    warnings=(),
                    errors=(),
                    started_at=started,
                    completed_at=completed,
                    duration_ms=(time.perf_counter() - start_perf) * 1000,
                    correlation_id=corr,
                )

            validate_kite_credentials(
                envelope.api_key,
                None,
                require_secret=False,
                min_length=self._config.min_credential_length,
                reject_placeholders=self._config.environment_profile
                is EnvironmentProfile.PRODUCTION,
            )
            if not envelope.access_token.strip():
                raise InvalidCredentialError(
                    "Persisted access token is missing.",
                    code="KITE_AUTH.CREDENTIAL.MISSING_ACCESS_TOKEN",
                    field="access_token",
                )

            now = self._clock()
            if is_session_expired(
                envelope.expires_at,
                now=now,
                skew_seconds=self._config.clock_skew_seconds,
            ):
                with self._lock:
                    self._status = AuthenticationStatus.EXPIRED
                    self._session = None
                raise SessionExpiredError(
                    "Restored session is expired.",
                    code="KITE_AUTH.SESSION.EXPIRED",
                    correlation_id=corr,
                )

            user_id = envelope.user_id
            user_name = envelope.user_name
            if self._config.require_profile_probe:
                probe_performed = True
                client = self._sdk_factory(envelope.api_key)
                user_id, user_name, probe_warnings = self._run_profile_probe(
                    client,
                    envelope.access_token,
                    user_id=user_id,
                    user_name=user_name,
                    required=True,
                )
                warnings.extend(probe_warnings)

            expected_fp = compute_session_fingerprint(
                session_id=envelope.session_id,
                api_key=envelope.api_key,
                access_token=envelope.access_token,
                authenticated_at=envelope.authenticated_at,
                expires_at=envelope.expires_at,
                user_id=user_id,
                environment_profile=envelope.environment_profile,
            )
            if (
                self._config.deterministic_fingerprint
                and expected_fp != envelope.session_fingerprint
                and user_id == envelope.user_id
            ):
                # Allow fingerprint drift only when probe enriched user_id; otherwise fail.
                if user_id == envelope.user_id and user_name == envelope.user_name:
                    raise AuthenticationError(
                        "Restored session fingerprint mismatch.",
                        code="KITE_AUTH.SESSION.FINGERPRINT_MISMATCH",
                        correlation_id=corr,
                    )

            session = KiteSession(
                session_id=envelope.session_id,
                broker_id=BrokerId.KITE,
                api_key=envelope.api_key,
                access_token=envelope.access_token,
                user_id=user_id,
                user_name=user_name,
                authenticated_at=envelope.authenticated_at,
                expires_at=envelope.expires_at,
                session_fingerprint=compute_session_fingerprint(
                    session_id=envelope.session_id,
                    api_key=envelope.api_key,
                    access_token=envelope.access_token,
                    authenticated_at=envelope.authenticated_at,
                    expires_at=envelope.expires_at,
                    user_id=user_id,
                    environment_profile=envelope.environment_profile,
                ),
                environment_profile=EnvironmentProfile(envelope.environment_profile),
            )
            validate_kite_session(session)
            broker_session = project_broker_session(session)
            with self._lock:
                self._session = session
                self._api_key = session.api_key
                self._status = AuthenticationStatus.AUTHENTICATED
                self._last_error_code = None
                self._last_error_message = None
            completed = self._clock()
            _LOGGER.info("kite_auth.restore.success")
            return AuthenticationResult(
                result_id=str(uuid.uuid4()),
                status=AuthenticationStatus.AUTHENTICATED,
                session=session,
                broker_session=broker_session,
                metadata=self._build_metadata(
                    token_source=TokenSource.RESTORED,
                    fingerprint=session.session_fingerprint,
                    probe_performed=probe_performed,
                ),
                warnings=tuple(warnings),
                errors=(),
                started_at=started,
                completed_at=completed,
                duration_ms=(time.perf_counter() - start_perf) * 1000,
                correlation_id=corr,
            )
        except AuthenticationError as exc:
            return self._failure_result(
                started=started,
                start_perf=start_perf,
                corr=corr,
                exc=exc,
                token_source=TokenSource.RESTORED,
                warnings=warnings,
                metadata=None,
                probe_performed=probe_performed,
            )

    def refresh_session(
        self,
        *,
        correlation_id: str | None = None,
    ) -> AuthenticationResult:
        """Refresh the current Kite session.

        Zerodha access tokens do not support silent OAuth-style refresh —
        a token stays valid until its end-of-day expiry (or explicit logout)
        and can only be replaced by a fresh interactive login. "Refresh"
        here means: (1) re-read the token store in case an external login
        flow (e.g. an operator re-running the login script) persisted a
        newer token than the one currently held in memory, adopting it when
        different; otherwise (2) perform a lightweight remote profile probe
        against the existing in-memory session to confirm it is still
        accepted upstream and update ``last_validated_at``. Falls back to
        :meth:`restore_session` when no in-memory session exists.

        Args:
            correlation_id: Optional correlation id.

        Returns:
            Structured authentication result.
        """
        started = self._clock()
        start_perf = time.perf_counter()
        corr = correlation_id or str(uuid.uuid4())
        with self._lock:
            current = self._session
        if current is None:
            return self.restore_session(correlation_id=corr)

        try:
            envelope = self._token_store.load()
        except TokenPersistenceError:
            envelope = None
        if envelope is not None and envelope.access_token != current.access_token:
            return self.restore_session(correlation_id=corr)

        now = self._clock()
        if self._config.fail_closed_on_expiry and is_session_expired(
            current.expires_at,
            now=now,
            skew_seconds=self._config.clock_skew_seconds,
        ):
            with self._lock:
                self._status = AuthenticationStatus.EXPIRED
                self._session = None
            return self._failure_result(
                started=started,
                start_perf=start_perf,
                corr=corr,
                exc=SessionExpiredError(
                    "Session is expired.",
                    code="KITE_AUTH.SESSION.EXPIRED",
                    correlation_id=corr,
                ),
                token_source=TokenSource.RESTORED,
                warnings=[],
                metadata=None,
                probe_performed=False,
            )

        try:
            client = self._sdk_factory(current.api_key)
            _user_id, _user_name, warnings = self._run_profile_probe(
                client,
                current.access_token,
                user_id=current.user_id,
                user_name=current.user_name,
                required=True,
            )
        except AuthenticationError as exc:
            return self._failure_result(
                started=started,
                start_perf=start_perf,
                corr=corr,
                exc=exc,
                token_source=TokenSource.RESTORED,
                warnings=[],
                metadata=None,
                probe_performed=True,
            )

        with self._lock:
            self._last_validated_at = now
            self._status = AuthenticationStatus.AUTHENTICATED
            self._last_error_code = None
            self._last_error_message = None
        broker_session = project_broker_session(current)
        completed = self._clock()
        _LOGGER.info("kite_auth.refresh.success")
        return AuthenticationResult(
            result_id=str(uuid.uuid4()),
            status=AuthenticationStatus.AUTHENTICATED,
            session=current,
            broker_session=broker_session,
            metadata=self._build_metadata(
                token_source=TokenSource.RESTORED,
                fingerprint=current.session_fingerprint,
                probe_performed=True,
            ),
            warnings=tuple(warnings),
            errors=(),
            started_at=started,
            completed_at=completed,
            duration_ms=(time.perf_counter() - start_perf) * 1000,
            correlation_id=corr,
        )

    def auto_reconnect(
        self,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        backoff_multiplier: float = 2.0,
        sleep: Callable[[float], None] | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticationResult:
        """Attempt to (re)establish a healthy Kite session with bounded retries.

        Repeatedly calls :meth:`refresh_session` (which itself falls back to
        :meth:`restore_session`) up to ``max_attempts`` times with exponential
        backoff between attempts, stopping as soon as a healthy
        ``AUTHENTICATED``/``DEGRADED`` result is obtained. Never raises — the
        caller inspects the returned result's ``status``. Does not issue a
        new login URL or exchange a request token: those require an
        interactive operator and stay out of scope for unattended
        reconnection.

        Args:
            max_attempts: Maximum reconnect attempts (>= 1).
            backoff_seconds: Initial delay between attempts, in seconds.
            backoff_multiplier: Multiplier applied to the delay after each
                failed attempt.
            sleep: Injectable sleep function for deterministic tests;
                defaults to :func:`time.sleep`.
            correlation_id: Optional correlation id shared across attempts.

        Returns:
            The first healthy result, or the last failure result after
            exhausting ``max_attempts``.
        """
        if max_attempts < 1:
            raise AuthenticationError(
                "max_attempts must be >= 1.",
                code="KITE_AUTH.CONFIG.INVALID",
                field="max_attempts",
            )
        sleeper = sleep or time.sleep
        corr = correlation_id or str(uuid.uuid4())
        delay = backoff_seconds
        result: AuthenticationResult | None = None
        for attempt in range(1, max_attempts + 1):
            result = self.refresh_session(correlation_id=corr)
            if result.status in {
                AuthenticationStatus.AUTHENTICATED,
                AuthenticationStatus.DEGRADED,
            }:
                return result
            if attempt < max_attempts:
                sleeper(delay)
                delay *= backoff_multiplier
        assert result is not None
        return result

    def inject_access_token(
        self,
        access_token: str,
        *,
        api_key: str | None = None,
        expires_at: datetime | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticationResult:
        """Inject an access token without performing request-token exchange.

        Args:
            access_token: Access token value.
            api_key: Optional API key override.
            expires_at: Optional explicit expiry.
            correlation_id: Optional correlation id.

        Returns:
            Structured authentication result.
        """
        started = self._clock()
        start_perf = time.perf_counter()
        corr = correlation_id or str(uuid.uuid4())
        warnings: list[AuthenticationWarningRecord] = []
        probe_performed = False
        try:
            key = api_key or self._api_key
            if not key:
                raise InvalidCredentialError(
                    "API key is required for token injection.",
                    code="KITE_AUTH.CREDENTIAL.MISSING_API_KEY",
                    field="api_key",
                )
            if not access_token or not access_token.strip():
                raise InvalidCredentialError(
                    "access_token is required.",
                    code="KITE_AUTH.CREDENTIAL.MISSING_ACCESS_TOKEN",
                    field="access_token",
                )
            validate_kite_credentials(
                key,
                None,
                require_secret=False,
                min_length=self._config.min_credential_length,
                reject_placeholders=self._config.environment_profile
                is EnvironmentProfile.PRODUCTION,
            )
            token = access_token.strip()
            user_id: str | None = None
            user_name: str | None = None
            if self._config.require_profile_probe:
                probe_performed = True
                client = self._sdk_factory(key)
                user_id, user_name, probe_warnings = self._run_profile_probe(
                    client,
                    token,
                    user_id=None,
                    user_name=None,
                    required=True,
                )
                warnings.extend(probe_warnings)

            authenticated_at = started if _is_timezone_aware(started) else _utc_now()
            if expires_at is not None and not _is_timezone_aware(expires_at):
                raise AuthenticationError(
                    "expires_at must be timezone-aware.",
                    code="KITE_AUTH.SESSION.NAIVE_TIMESTAMP",
                    field="expires_at",
                )
            assigned_expiry = self._assign_expiry(authenticated_at, explicit=expires_at)
            if is_session_expired(
                assigned_expiry,
                now=self._clock(),
                skew_seconds=self._config.clock_skew_seconds,
            ):
                raise SessionExpiredError(
                    "Injected session is expired.",
                    code="KITE_AUTH.SESSION.EXPIRED",
                    correlation_id=corr,
                )
            session = self._seal_session(
                api_key=key,
                access_token=token,
                authenticated_at=authenticated_at,
                expires_at=assigned_expiry,
                user_id=user_id,
                user_name=user_name,
                exchange_metadata={},
                metadata={"token_source": TokenSource.INJECTED.value},
            )
            self._persist_session(session)
            broker_session = project_broker_session(session)
            with self._lock:
                self._session = session
                self._api_key = key
                self._status = AuthenticationStatus.AUTHENTICATED
            completed = self._clock()
            return AuthenticationResult(
                result_id=str(uuid.uuid4()),
                status=AuthenticationStatus.AUTHENTICATED,
                session=session,
                broker_session=broker_session,
                metadata=self._build_metadata(
                    token_source=TokenSource.INJECTED,
                    fingerprint=session.session_fingerprint,
                    probe_performed=probe_performed,
                ),
                warnings=tuple(warnings),
                errors=(),
                started_at=started,
                completed_at=completed,
                duration_ms=(time.perf_counter() - start_perf) * 1000,
                correlation_id=corr,
            )
        except AuthenticationError as exc:
            return self._failure_result(
                started=started,
                start_perf=start_perf,
                corr=corr,
                exc=exc,
                token_source=TokenSource.INJECTED,
                warnings=warnings,
                metadata=None,
                probe_performed=probe_performed,
            )

    def logout(
        self,
        *,
        invalidate_remote: bool = True,
        correlation_id: str | None = None,
    ) -> AuthenticationResult:
        """Clear local session material and optionally invalidate remotely.

        Args:
            invalidate_remote: Attempt SDK token invalidation when available.
            correlation_id: Optional correlation id.

        Returns:
            Structured logout result with status ``LOGGED_OUT``.
        """
        started = self._clock()
        start_perf = time.perf_counter()
        corr = correlation_id or str(uuid.uuid4())
        warnings: list[AuthenticationWarningRecord] = []
        with self._lock:
            session = self._session
            api_key = self._api_key
            self._status = AuthenticationStatus.REVOKED

        if invalidate_remote and session is not None and api_key:
            try:
                client = self._sdk_factory(api_key)
                if hasattr(client, "set_access_token"):
                    client.set_access_token(session.access_token)
                invalidate = getattr(client, "invalidate_access_token", None)
                if callable(invalidate):
                    invalidate()
            except Exception as exc:
                warnings.append(
                    AuthenticationWarningRecord(
                        code="KITE_AUTH.PROBE.FAILED",
                        message=f"Remote invalidate failed: {_redact_message(str(exc))}",
                    )
                )

        try:
            self._token_store.clear()
        except TokenPersistenceError as exc:
            warnings.append(
                AuthenticationWarningRecord(code=exc.code, message=str(exc))
            )

        with self._lock:
            self._session = None
            self._status = AuthenticationStatus.LOGGED_OUT
            self._last_error_code = None
            self._last_error_message = None
        completed = self._clock()
        _LOGGER.info("kite_auth.logout.completed")
        return AuthenticationResult(
            result_id=str(uuid.uuid4()),
            status=AuthenticationStatus.LOGGED_OUT,
            session=None,
            broker_session=None,
            metadata=self._build_metadata(
                token_source=TokenSource.NONE,
                fingerprint=None,
                probe_performed=False,
            ),
            warnings=tuple(warnings),
            errors=(),
            started_at=started,
            completed_at=completed,
            duration_ms=(time.perf_counter() - start_perf) * 1000,
            correlation_id=corr,
        )

    def require_broker_session(self) -> BrokerSession:
        """Return the projected broker session or raise if unavailable/expired.

        Returns:
            Current :class:`BrokerSession`.

        Raises:
            SessionExpiredError: When session is expired under fail-closed policy.
            AuthenticationError: When no authenticated session exists.
        """
        with self._lock:
            status = self._status
            session = self._session
        if session is None or status not in {
            AuthenticationStatus.AUTHENTICATED,
            AuthenticationStatus.DEGRADED,
        }:
            if status in {
                AuthenticationStatus.EXPIRED,
                AuthenticationStatus.REVOKED,
                AuthenticationStatus.LOGGED_OUT,
            }:
                raise SessionExpiredError(
                    "Session is not authenticated.",
                    code="KITE_AUTH.SESSION.EXPIRED",
                )
            raise AuthenticationError(
                "No authenticated session available.",
                code="KITE_AUTH.SESSION.NOT_AUTHENTICATED",
            )
        if self._config.fail_closed_on_expiry and is_session_expired(
            session.expires_at,
            now=self._clock(),
            skew_seconds=self._config.clock_skew_seconds,
        ):
            with self._lock:
                self._status = AuthenticationStatus.EXPIRED
            raise SessionExpiredError(
                "Session is expired.",
                code="KITE_AUTH.SESSION.EXPIRED",
            )
        return project_broker_session(session)

    def clear_persisted_session(self) -> None:
        """Clear the configured token store without changing in-memory status."""
        self._token_store.clear()

    def get_health(self, *, verify: bool = False) -> AuthenticationHealthReport:
        """Return an aggregated authentication health snapshot.

        Args:
            verify: When ``True`` and an ``AUTHENTICATED``/``DEGRADED`` session
                is present, perform a lightweight live profile probe against
                Kite to confirm connectivity before reporting health (adds one
                upstream API call). A token-shaped failure marks the session
                ``EXPIRED``; defaults to ``False`` for a fast, local-only,
                network-free status read.

        Returns:
            Aggregated health snapshot, including live-verification results
            when ``verify=True``.
        """
        verify_issue: AuthenticationHealthIssue | None = None
        if verify:
            with self._lock:
                probe_session = self._session
                probe_status = self._status
            if probe_session is not None and probe_status in {
                AuthenticationStatus.AUTHENTICATED,
                AuthenticationStatus.DEGRADED,
            }:
                try:
                    client = self._sdk_factory(probe_session.api_key)
                    self._run_profile_probe(
                        client,
                        probe_session.access_token,
                        user_id=probe_session.user_id,
                        user_name=probe_session.user_name,
                        required=True,
                    )
                    with self._lock:
                        self._last_validated_at = self._clock()
                        self._last_error_code = None
                        self._last_error_message = None
                except AuthenticationError as exc:
                    with self._lock:
                        self._last_error_code = exc.code
                        self._last_error_message = _redact_message(str(exc))
                        if isinstance(exc, SessionExpiredError):
                            self._status = AuthenticationStatus.EXPIRED
                            self._session = None
                    verify_issue = AuthenticationHealthIssue(
                        issue_code="KITE_AUTH.HEALTH.CONNECTION_PROBE_FAILED",
                        severity="error",
                        message=f"Live connection probe failed: {_redact_message(str(exc))}",
                    )

        with self._lock:
            status = self._status
            session = self._session
            has_api_key = bool(self._api_key)
            has_api_secret = bool(self._api_secret)
            last_error_code = self._last_error_code
            last_error_message = self._last_error_message
            last_validated_at = self._last_validated_at
        now = self._clock()
        has_access_token = bool(session and session.access_token)
        expires_at = session.expires_at if session else None
        expired = is_session_expired(
            expires_at,
            now=now,
            skew_seconds=self._config.clock_skew_seconds,
        )
        seconds_to_expiry: float | None = None
        if expires_at is not None:
            seconds_to_expiry = (expires_at - now).total_seconds()
        issues: list[AuthenticationHealthIssue] = []
        if not has_access_token and status is AuthenticationStatus.AUTHENTICATED:
            issues.append(
                AuthenticationHealthIssue(
                    issue_code="KITE_AUTH.HEALTH.TOKEN_MISSING",
                    severity="error",
                    message="Authenticated status without access token.",
                )
            )
        if expired:
            issues.append(
                AuthenticationHealthIssue(
                    issue_code="KITE_AUTH.HEALTH.TOKEN_EXPIRED",
                    severity="error",
                    message="Access token is expired.",
                )
            )
        if seconds_to_expiry is not None and 0 <= seconds_to_expiry < 1800:
            issues.append(
                AuthenticationHealthIssue(
                    issue_code="KITE_AUTH.HEALTH.TOKEN_EXPIRING_SOON",
                    severity="warning",
                    message="Access token expires within 30 minutes.",
                )
            )
        if not has_api_secret:
            issues.append(
                AuthenticationHealthIssue(
                    issue_code="KITE_AUTH.HEALTH.SECRET_MISSING",
                    severity="warning",
                    message="API secret is not loaded (ok for restore-only).",
                )
            )
        persistence_available = self._token_store.is_available()
        if not persistence_available:
            issues.append(
                AuthenticationHealthIssue(
                    issue_code="KITE_AUTH.HEALTH.STORE_UNAVAILABLE",
                    severity="warning",
                    message="Token store is unavailable.",
                )
            )
        if verify_issue is not None:
            issues.append(verify_issue)
        overall = self._derive_overall_health(
            status=status,
            has_access_token=has_access_token,
            expired=expired,
        )
        return AuthenticationHealthReport(
            report_id=str(uuid.uuid4()),
            as_of=now,
            status=status,
            overall_health=overall,
            has_api_key=has_api_key,
            has_api_secret=has_api_secret,
            has_access_token=has_access_token,
            session_id=session.session_id if session else None,
            expires_at=expires_at,
            seconds_to_expiry=seconds_to_expiry,
            is_expired=expired,
            persistence_available=persistence_available,
            last_error_code=last_error_code,
            last_error_message=last_error_message,
            session_fingerprint=session.session_fingerprint if session else None,
            issues=tuple(issues),
            last_validated_at=last_validated_at,
        )

    def _derive_overall_health(
        self,
        *,
        status: AuthenticationStatus,
        has_access_token: bool,
        expired: bool,
    ) -> AuthHealthStatus:
        if status in {
            AuthenticationStatus.FAILED,
            AuthenticationStatus.EXPIRED,
            AuthenticationStatus.REVOKED,
        } or expired:
            return AuthHealthStatus.UNHEALTHY
        if (
            self._config.environment_profile is EnvironmentProfile.PRODUCTION
            and not has_access_token
            and status
            not in {
                AuthenticationStatus.UNAUTHENTICATED,
                AuthenticationStatus.CREDENTIALS_LOADED,
                AuthenticationStatus.AWAITING_REQUEST_TOKEN,
                AuthenticationStatus.LOGGED_OUT,
            }
        ):
            return AuthHealthStatus.UNHEALTHY
        if status is AuthenticationStatus.AUTHENTICATED and not expired:
            return AuthHealthStatus.HEALTHY
        if status is AuthenticationStatus.DEGRADED:
            return AuthHealthStatus.DEGRADED
        return AuthHealthStatus.UNKNOWN

    def _run_profile_probe(
        self,
        client: Any,
        access_token: str,
        *,
        user_id: str | None,
        user_name: str | None,
        required: bool,
    ) -> tuple[str | None, str | None, list[AuthenticationWarningRecord]]:
        warnings: list[AuthenticationWarningRecord] = []
        try:
            if hasattr(client, "set_access_token"):
                client.set_access_token(access_token)
            profile = client.profile()
            if isinstance(profile, Mapping):
                user_id = user_id or (
                    str(profile["user_id"]) if profile.get("user_id") else None
                )
                user_name = user_name or (
                    str(profile["user_name"]) if profile.get("user_name") else None
                )
        except Exception as exc:
            raw = str(exc)
            message = _redact_message(raw)
            # Classify using the raw exception text; redaction may remove
            # token-like substrings needed for TokenException detection.
            lowered = raw.lower()
            if required:
                if "token" in lowered or "403" in lowered or "auth" in lowered:
                    raise SessionExpiredError(
                        f"Profile probe failed: {message}",
                        code="KITE_AUTH.SESSION.EXPIRED",
                    ) from exc
                raise AuthenticationError(
                    f"Profile probe failed: {message}",
                    code="KITE_AUTH.PROBE.FAILED",
                ) from exc
            warnings.append(
                AuthenticationWarningRecord(
                    code="KITE_AUTH.PROBE.FAILED",
                    message=f"Optional profile probe failed: {message}",
                )
            )
        return user_id, user_name, warnings

    def _assign_expiry(
        self,
        authenticated_at: datetime,
        *,
        explicit: datetime | None,
    ) -> datetime | None:
        if explicit is not None:
            return explicit
        policy = self._config.default_expiry_policy
        if policy is ExpiryPolicyKind.NONE or policy is ExpiryPolicyKind.EXPLICIT:
            return None
        if policy is ExpiryPolicyKind.FIXED_HOURS:
            return authenticated_at + timedelta(hours=self._config.fixed_ttl_hours)
        return compute_next_0600_ist(authenticated_at)

    def _seal_session(
        self,
        *,
        api_key: str,
        access_token: str,
        authenticated_at: datetime,
        expires_at: datetime | None,
        user_id: str | None,
        user_name: str | None,
        exchange_metadata: Mapping[str, str],
        metadata: Mapping[str, str] | None,
    ) -> KiteSession:
        session_id = str(uuid.uuid4())
        fingerprint = compute_session_fingerprint(
            session_id=session_id,
            api_key=api_key,
            access_token=access_token,
            authenticated_at=authenticated_at,
            expires_at=expires_at,
            user_id=user_id,
            environment_profile=self._config.environment_profile.value,
        )
        session = KiteSession(
            session_id=session_id,
            broker_id=BrokerId.KITE,
            api_key=api_key,
            access_token=access_token,
            user_id=user_id,
            user_name=user_name,
            authenticated_at=authenticated_at,
            expires_at=expires_at,
            exchange_metadata=MappingProxyType(dict(exchange_metadata)),
            session_fingerprint=fingerprint,
            environment_profile=self._config.environment_profile,
            metadata=MappingProxyType(dict(metadata or {})),
        )
        return validate_kite_session(session)

    def _persist_session(self, session: KiteSession) -> None:
        if self._config.persistence_mode is TokenPersistenceMode.DISABLED:
            return
        if (
            self._config.persistence_mode is TokenPersistenceMode.ENV_FILE
            and self._config.environment_profile is EnvironmentProfile.PRODUCTION
            and not self._config.allow_env_file_persistence
        ):
            raise TokenPersistenceError(
                "ENV_FILE persistence is forbidden in production.",
                code="KITE_AUTH.PERSIST.MODE_FORBIDDEN",
            )
        draft = TokenEnvelope(
            schema_version=TOKEN_ENVELOPE_SCHEMA_VERSION,
            session_id=session.session_id,
            api_key=session.api_key,
            access_token=session.access_token,
            user_id=session.user_id,
            user_name=session.user_name,
            authenticated_at=session.authenticated_at,
            expires_at=session.expires_at,
            session_fingerprint=session.session_fingerprint,
            environment_profile=session.environment_profile.value,
            checksum="",
        )
        envelope = replace(draft, checksum=compute_envelope_checksum(draft))
        self._token_store.save(envelope)
        _LOGGER.info("kite_auth.persist.success")

    def _build_metadata(
        self,
        *,
        token_source: TokenSource,
        fingerprint: str | None,
        probe_performed: bool,
        audit_tags: Mapping[str, str] | None = None,
    ) -> SessionMetadata:
        backend: str | None
        mode = self._config.persistence_mode
        if mode is TokenPersistenceMode.DISABLED:
            backend = "null"
        elif mode is TokenPersistenceMode.FILE:
            backend = "file"
        elif mode is TokenPersistenceMode.ENV_FILE:
            backend = "env_file"
        else:
            backend = "custom"
        with self._lock:
            login_url_issued = self._login_url_issued
            credential_source = self._credential_source
        return SessionMetadata(
            environment_profile=self._config.environment_profile,
            runner_kind=self._config.runner_kind,
            credential_source=credential_source,
            token_source=token_source,
            persistence_backend=backend,
            login_url_issued=login_url_issued,
            profile_probe_performed=probe_performed,
            clock_skew_seconds=self._config.clock_skew_seconds,
            session_fingerprint=fingerprint,
            audit_tags=MappingProxyType(dict(audit_tags or {})),
        )

    def _failure_result(
        self,
        *,
        started: datetime,
        start_perf: float,
        corr: str,
        exc: AuthenticationError,
        token_source: TokenSource,
        warnings: list[AuthenticationWarningRecord],
        metadata: Mapping[str, str] | None,
        probe_performed: bool,
    ) -> AuthenticationResult:
        if isinstance(exc, SessionExpiredError):
            status = AuthenticationStatus.EXPIRED
        else:
            status = AuthenticationStatus.FAILED
        with self._lock:
            self._status = status
            self._last_error_code = exc.code
            self._last_error_message = _redact_message(str(exc))
            if status is AuthenticationStatus.EXPIRED:
                self._session = None
        completed = self._clock()
        _LOGGER.error(
            "kite_auth.exchange.failed"
            if token_source is TokenSource.EXCHANGE
            else "kite_auth.restore.failed",
            extra={"code": exc.code},
        )
        return AuthenticationResult(
            result_id=str(uuid.uuid4()),
            status=status,
            session=None,
            broker_session=None,
            metadata=self._build_metadata(
                token_source=token_source,
                fingerprint=None,
                probe_performed=probe_performed,
                audit_tags=metadata,
            ),
            warnings=tuple(warnings),
            errors=(
                AuthenticationErrorRecord(
                    code=exc.code,
                    message=_redact_message(str(exc)),
                    field=exc.field,
                ),
            ),
            started_at=started,
            completed_at=completed,
            duration_ms=(time.perf_counter() - start_perf) * 1000,
            correlation_id=corr,
        )


def authenticate_from_request_token(
    request_token: str,
    *,
    config: KiteAuthenticationConfig | None = None,
    secret_provider: SecretProvider | None = None,
    **kwargs: Any,
) -> AuthenticationResult:
    """One-shot authenticate helper for CLI wrappers.

    Args:
        request_token: Kite request token.
        config: Optional authentication config.
        secret_provider: Optional secret provider.
        **kwargs: Forwarded to :class:`KiteAuthenticator`.

    Returns:
        Authentication result.
    """
    auth = KiteAuthenticator(config, secret_provider=secret_provider, **kwargs)
    return auth.authenticate(request_token)


def restore_or_authenticate(
    request_token: str | None,
    *,
    config: KiteAuthenticationConfig | None = None,
    **kwargs: Any,
) -> AuthenticationResult:
    """Prefer restore; if unavailable/expired and request_token provided, exchange.

    Args:
        request_token: Optional request token for fallback exchange.
        config: Optional authentication config.
        **kwargs: Forwarded to :class:`KiteAuthenticator`.

    Returns:
        Authentication result.
    """
    auth = KiteAuthenticator(config, **kwargs)
    result = auth.restore_session()
    if result.status is AuthenticationStatus.AUTHENTICATED:
        return result
    if request_token:
        return auth.authenticate(request_token)
    return result


def serialize_kite_session(
    session: KiteSession,
    *,
    include_secrets: bool = False,
    include_api_key: bool = False,
) -> str:
    """Serialize a Kite session to JSON, redacting secrets by default.

    Args:
        session: Session to serialize.
        include_secrets: When ``True``, include access_token (tests only).
        include_api_key: When ``True``, include raw api_key.

    Returns:
        JSON string.
    """
    payload = {
        "schema_version": KITE_AUTHENTICATION_SCHEMA_VERSION,
        "session_id": session.session_id,
        "broker_id": session.broker_id.value,
        "api_key": session.api_key if include_api_key else redact_secret(session.api_key),
        "access_token": (
            session.access_token if include_secrets else redact_secret(session.access_token)
        ),
        "user_id": session.user_id,
        "user_name": session.user_name,
        "authenticated_at": _isoformat_utc(session.authenticated_at),
        "expires_at": _isoformat_utc(session.expires_at) if session.expires_at else None,
        "login_time": _isoformat_utc(session.login_time) if session.login_time else None,
        "exchange_metadata": dict(session.exchange_metadata),
        "session_fingerprint": session.session_fingerprint,
        "environment_profile": session.environment_profile.value,
        "metadata": dict(session.metadata),
    }
    return json.dumps(payload, sort_keys=True)


def serialize_session_metadata(metadata: SessionMetadata) -> str:
    """Serialize session metadata to JSON."""
    payload = {
        "schema_version": KITE_AUTHENTICATION_SCHEMA_VERSION,
        "environment_profile": metadata.environment_profile.value,
        "runner_kind": metadata.runner_kind,
        "credential_source": metadata.credential_source.value,
        "token_source": metadata.token_source.value,
        "persistence_backend": metadata.persistence_backend,
        "login_url_issued": metadata.login_url_issued,
        "profile_probe_performed": metadata.profile_probe_performed,
        "clock_skew_seconds": metadata.clock_skew_seconds,
        "session_fingerprint": metadata.session_fingerprint,
        "audit_tags": dict(metadata.audit_tags),
    }
    return json.dumps(payload, sort_keys=True)


def serialize_authentication_result(result: AuthenticationResult) -> str:
    """Serialize authentication result with redacted session secrets."""
    payload: dict[str, Any] = {
        "schema_version": KITE_AUTHENTICATION_SCHEMA_VERSION,
        "result_id": result.result_id,
        "status": result.status.value,
        "session": (
            json.loads(serialize_kite_session(result.session)) if result.session else None
        ),
        "broker_session": None,
        "metadata": json.loads(serialize_session_metadata(result.metadata)),
        "warnings": [
            {"code": w.code, "message": w.message, "field": w.field} for w in result.warnings
        ],
        "errors": [
            {"code": e.code, "message": e.message, "field": e.field} for e in result.errors
        ],
        "started_at": _isoformat_utc(result.started_at),
        "completed_at": _isoformat_utc(result.completed_at),
        "duration_ms": result.duration_ms,
        "correlation_id": result.correlation_id,
    }
    if result.broker_session is not None:
        payload["broker_session"] = {
            "broker_id": result.broker_session.broker_id.value,
            "session_id": result.broker_session.session_id,
            "authenticated_at": _isoformat_utc(result.broker_session.authenticated_at),
            "expires_at": (
                _isoformat_utc(result.broker_session.expires_at)
                if result.broker_session.expires_at
                else None
            ),
        }
    return json.dumps(payload, sort_keys=True)


def deserialize_authentication_result(payload: str) -> AuthenticationResult:
    """Deserialize authentication result from JSON (session tokens remain redacted)."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AuthenticationError(
            f"Malformed JSON: {exc}",
            code="KITE_AUTH.SERIALIZATION.MALFORMED",
        ) from exc
    if data.get("schema_version") != KITE_AUTHENTICATION_SCHEMA_VERSION:
        raise AuthenticationError(
            "Unsupported schema version.",
            code="KITE_AUTH.SERIALIZATION.UNSUPPORTED_VERSION",
        )
    session = None
    if data.get("session") is not None:
        raw = data["session"]
        # Redacted tokens cannot rebuild a usable live session; reconstruct placeholder.
        access = str(raw.get("access_token", ""))
        if access in {"<redacted>", "<missing>", ""}:
            access = "REDACTED_TOKEN_PLACEHOLDER"
        api_key = str(raw.get("api_key", ""))
        if api_key in {"<redacted>", "<missing>", ""}:
            api_key = "REDACTED_KEY"
        session = KiteSession(
            session_id=str(raw["session_id"]),
            broker_id=BrokerId(raw["broker_id"]),
            api_key=api_key,
            access_token=access,
            user_id=raw.get("user_id"),
            user_name=raw.get("user_name"),
            authenticated_at=_parse_iso_datetime(raw["authenticated_at"]),
            expires_at=(
                _parse_iso_datetime(raw["expires_at"]) if raw.get("expires_at") else None
            ),
            session_fingerprint=str(raw["session_fingerprint"]),
            environment_profile=EnvironmentProfile(raw["environment_profile"]),
            exchange_metadata=MappingProxyType(dict(raw.get("exchange_metadata", {}))),
            metadata=MappingProxyType(dict(raw.get("metadata", {}))),
        )
    meta_raw = data["metadata"]
    metadata = SessionMetadata(
        environment_profile=EnvironmentProfile(meta_raw["environment_profile"]),
        runner_kind=str(meta_raw["runner_kind"]),
        credential_source=CredentialSource(meta_raw["credential_source"]),
        token_source=TokenSource(meta_raw["token_source"]),
        persistence_backend=meta_raw.get("persistence_backend"),
        login_url_issued=bool(meta_raw.get("login_url_issued", False)),
        profile_probe_performed=bool(meta_raw.get("profile_probe_performed", False)),
        clock_skew_seconds=float(meta_raw.get("clock_skew_seconds", DEFAULT_CLOCK_SKEW_SECONDS)),
        session_fingerprint=meta_raw.get("session_fingerprint"),
        audit_tags=MappingProxyType(dict(meta_raw.get("audit_tags", {}))),
    )
    return AuthenticationResult(
        result_id=str(data["result_id"]),
        status=AuthenticationStatus(data["status"]),
        session=session,
        broker_session=None,
        metadata=metadata,
        warnings=tuple(
            AuthenticationWarningRecord(
                code=item["code"],
                message=item["message"],
                field=item.get("field"),
            )
            for item in data.get("warnings", [])
        ),
        errors=tuple(
            AuthenticationErrorRecord(
                code=item["code"],
                message=item["message"],
                field=item.get("field"),
            )
            for item in data.get("errors", [])
        ),
        started_at=_parse_iso_datetime(data["started_at"]),
        completed_at=_parse_iso_datetime(data["completed_at"]),
        duration_ms=float(data["duration_ms"]),
        correlation_id=str(data["correlation_id"]),
    )


def serialize_authentication_health_report(report: AuthenticationHealthReport) -> str:
    """Serialize authentication health report to JSON."""
    payload = {
        "schema_version": KITE_AUTHENTICATION_SCHEMA_VERSION,
        "report_id": report.report_id,
        "as_of": _isoformat_utc(report.as_of),
        "status": report.status.value,
        "overall_health": report.overall_health.value,
        "has_api_key": report.has_api_key,
        "has_api_secret": report.has_api_secret,
        "has_access_token": report.has_access_token,
        "session_id": report.session_id,
        "expires_at": _isoformat_utc(report.expires_at) if report.expires_at else None,
        "seconds_to_expiry": report.seconds_to_expiry,
        "is_expired": report.is_expired,
        "persistence_available": report.persistence_available,
        "last_error_code": report.last_error_code,
        "last_error_message": report.last_error_message,
        "session_fingerprint": report.session_fingerprint,
        "issues": [
            {
                "issue_code": issue.issue_code,
                "severity": issue.severity,
                "message": issue.message,
                "field": issue.field,
            }
            for issue in report.issues
        ],
        "last_validated_at": (
            _isoformat_utc(report.last_validated_at) if report.last_validated_at else None
        ),
        "metadata": dict(report.metadata),
    }
    return json.dumps(payload, sort_keys=True)


def deserialize_authentication_health_report(payload: str) -> AuthenticationHealthReport:
    """Deserialize authentication health report from JSON."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AuthenticationError(
            f"Malformed JSON: {exc}",
            code="KITE_AUTH.SERIALIZATION.MALFORMED",
        ) from exc
    if data.get("schema_version") != KITE_AUTHENTICATION_SCHEMA_VERSION:
        raise AuthenticationError(
            "Unsupported schema version.",
            code="KITE_AUTH.SERIALIZATION.UNSUPPORTED_VERSION",
        )
    return AuthenticationHealthReport(
        report_id=str(data["report_id"]),
        as_of=_parse_iso_datetime(data["as_of"]),
        status=AuthenticationStatus(data["status"]),
        overall_health=AuthHealthStatus(data["overall_health"]),
        has_api_key=bool(data["has_api_key"]),
        has_api_secret=bool(data["has_api_secret"]),
        has_access_token=bool(data["has_access_token"]),
        session_id=data.get("session_id"),
        expires_at=(
            _parse_iso_datetime(data["expires_at"]) if data.get("expires_at") else None
        ),
        seconds_to_expiry=(
            float(data["seconds_to_expiry"])
            if data.get("seconds_to_expiry") is not None
            else None
        ),
        is_expired=bool(data["is_expired"]),
        persistence_available=bool(data["persistence_available"]),
        last_error_code=data.get("last_error_code"),
        last_error_message=data.get("last_error_message"),
        session_fingerprint=data.get("session_fingerprint"),
        issues=tuple(
            AuthenticationHealthIssue(
                issue_code=item["issue_code"],
                severity=item["severity"],
                message=item["message"],
                field=item.get("field"),
            )
            for item in data.get("issues", [])
        ),
        last_validated_at=(
            _parse_iso_datetime(data["last_validated_at"])
            if data.get("last_validated_at")
            else None
        ),
        metadata=MappingProxyType(dict(data.get("metadata", {}))),
    )


# ---------------------------------------------------------------------------
# Dashboard status integration
#
# Pure, read-only presentation mappings from authentication/session state to
# the small duck-typed shape ``dashboard.dashboard_facade.DashboardIntegrationFacade``
# already soft-reads from its optional ``session`` (``get_health()`` /
# ``get_runtime_state()`` — see ``IntegrationSessionLike`` there). Kept inside
# this module so dashboard connection/status cards can reflect real Kite
# authentication state without importing the dashboard package here.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DashboardBrokerConnectionSnapshot:
    """``broker_connection`` sub-object shape expected by the dashboard facade."""

    state: str
    connected: bool


@dataclass(frozen=True)
class DashboardSessionHealthSnapshot:
    """``get_health()`` return shape expected by the dashboard facade."""

    session_state: str
    overall_status: str
    broker_connection: DashboardBrokerConnectionSnapshot
    message: str
    market_status: str = "UNKNOWN"


@dataclass(frozen=True)
class DashboardRuntimeStateSnapshot:
    """``get_runtime_state()`` return shape expected by the dashboard facade."""

    execution_mode: str
    market_status: str = "UNKNOWN"


_DASHBOARD_SESSION_STATE_BY_AUTH_STATUS: Final[Mapping[AuthenticationStatus, str]] = {
    AuthenticationStatus.AUTHENTICATED: "running",
    AuthenticationStatus.DEGRADED: "degraded",
    AuthenticationStatus.EXCHANGING: "degraded",
    AuthenticationStatus.UNAUTHENTICATED: "stopped",
    AuthenticationStatus.CREDENTIALS_LOADED: "stopped",
    AuthenticationStatus.AWAITING_REQUEST_TOKEN: "stopped",
    AuthenticationStatus.LOGGED_OUT: "stopped",
    AuthenticationStatus.EXPIRED: "failed",
    AuthenticationStatus.REVOKED: "failed",
    AuthenticationStatus.FAILED: "failed",
}

_DASHBOARD_OVERALL_STATUS_BY_HEALTH: Final[Mapping[AuthHealthStatus, str]] = {
    AuthHealthStatus.HEALTHY: "running",
    AuthHealthStatus.DEGRADED: "degraded",
    AuthHealthStatus.UNHEALTHY: "disconnected",
    AuthHealthStatus.UNKNOWN: "unknown",
}

_DASHBOARD_EXECUTION_MODE_BY_PROFILE: Final[Mapping[EnvironmentProfile, str]] = {
    EnvironmentProfile.PRODUCTION: "LIVE",
    EnvironmentProfile.PAPER: "PAPER",
    EnvironmentProfile.DEVELOPMENT: "ANALYSIS",
}


def build_dashboard_session_health(
    report: AuthenticationHealthReport,
    *,
    market_status: str = "UNKNOWN",
) -> DashboardSessionHealthSnapshot:
    """Map an :class:`AuthenticationHealthReport` to the dashboard's health shape.

    Read-only presentation mapping — never mutates authentication state and
    never fabricates values beyond relabeling already-known status fields.

    Args:
        report: Health snapshot from :meth:`KiteAuthenticator.get_health`.
        market_status: Optional market status label (this module has no
            market-data visibility; defaults to ``"UNKNOWN"``).

    Returns:
        Dashboard-shaped session health snapshot.
    """
    session_state = _DASHBOARD_SESSION_STATE_BY_AUTH_STATUS.get(report.status, "stopped")
    overall_status = _DASHBOARD_OVERALL_STATUS_BY_HEALTH.get(
        report.overall_health, "unknown"
    )
    connected = (
        report.has_access_token
        and not report.is_expired
        and report.status is AuthenticationStatus.AUTHENTICATED
    )
    broker_connection = DashboardBrokerConnectionSnapshot(
        state="connected" if connected else "disconnected",
        connected=connected,
    )
    message = report.last_error_message or (
        "Kite session authenticated" if connected else "Kite session not authenticated"
    )
    return DashboardSessionHealthSnapshot(
        session_state=session_state,
        overall_status=overall_status,
        broker_connection=broker_connection,
        message=message,
        market_status=market_status,
    )


def build_dashboard_runtime_state(
    config: KiteAuthenticationConfig,
    *,
    market_status: str = "UNKNOWN",
) -> DashboardRuntimeStateSnapshot:
    """Map authentication config profile to the dashboard's runtime-state shape.

    Args:
        config: Active authentication configuration.
        market_status: Optional market status label.

    Returns:
        Dashboard-shaped runtime state snapshot.
    """
    execution_mode = _DASHBOARD_EXECUTION_MODE_BY_PROFILE.get(
        config.environment_profile, "ANALYSIS"
    )
    return DashboardRuntimeStateSnapshot(
        execution_mode=execution_mode,
        market_status=market_status,
    )


class KiteAuthDashboardSession:
    """Adapter exposing :class:`KiteAuthenticator` status to the dashboard.

    Implements the ``get_health()`` / ``get_runtime_state()`` surface that
    ``dashboard.dashboard_facade.DashboardIntegrationFacade`` duck-types as
    its optional ``session``. Pass an instance of this adapter as
    ``DashboardIntegrationFacade(session=KiteAuthDashboardSession(authenticator))``
    so dashboard connection/status cards reflect real Kite authentication
    state. Read-only — never authenticates, refreshes, reconnects, or
    otherwise mutates session state; callers drive the authenticator's
    lifecycle independently (e.g. via :meth:`KiteAuthenticator.auto_reconnect`).
    """

    def __init__(
        self,
        authenticator: KiteAuthenticator,
        *,
        market_status: str = "UNKNOWN",
        verify_connection: bool = False,
    ) -> None:
        """Wrap an authenticator for dashboard status reads.

        Args:
            authenticator: Live :class:`KiteAuthenticator` instance.
            market_status: Optional market status label to surface.
            verify_connection: When ``True``, each ``get_health()`` call
                performs a live upstream probe (one extra Kite API call);
                defaults to ``False`` for a purely local, low-cost read.
        """
        self._authenticator = authenticator
        self._market_status = market_status
        self._verify_connection = verify_connection

    def get_health(self) -> DashboardSessionHealthSnapshot:
        """Return the dashboard-shaped authentication health snapshot."""
        report = self._authenticator.get_health(verify=self._verify_connection)
        return build_dashboard_session_health(report, market_status=self._market_status)

    def get_runtime_state(self) -> DashboardRuntimeStateSnapshot:
        """Return the dashboard-shaped runtime state snapshot."""
        return build_dashboard_runtime_state(
            self._authenticator.config,
            market_status=self._market_status,
        )


__all__ = [
    "KITE_AUTHENTICATION_VERSION",
    "KITE_AUTHENTICATION_SCHEMA_VERSION",
    "TOKEN_ENVELOPE_SCHEMA_VERSION",
    "PRODUCER_NAME",
    "DEFAULT_STORE_PATH",
    "DEFAULT_CLOCK_SKEW_SECONDS",
    "IST_ZONE",
    "AuthenticationStatus",
    "AuthHealthStatus",
    "CredentialSource",
    "TokenSource",
    "TokenPersistenceMode",
    "ExpiryPolicyKind",
    "KiteAuthenticationConfig",
    "KiteSession",
    "AuthenticationResult",
    "SessionMetadata",
    "AuthenticationHealthReport",
    "AuthenticationWarningRecord",
    "AuthenticationErrorRecord",
    "AuthenticationHealthIssue",
    "TokenEnvelope",
    "AuthenticationError",
    "SessionExpiredError",
    "InvalidCredentialError",
    "TokenPersistenceError",
    "KiteAuthenticator",
    "TokenStore",
    "FileTokenStore",
    "EnvFileTokenStore",
    "NullTokenStore",
    "default_kite_authentication_config",
    "validate_kite_credentials",
    "validate_kite_session",
    "compute_session_fingerprint",
    "compute_next_0600_ist",
    "is_session_expired",
    "project_broker_session",
    "compute_envelope_checksum",
    "redact_secret",
    "authenticate_from_request_token",
    "restore_or_authenticate",
    "serialize_authentication_result",
    "deserialize_authentication_result",
    "serialize_authentication_health_report",
    "deserialize_authentication_health_report",
    "serialize_session_metadata",
    "DashboardBrokerConnectionSnapshot",
    "DashboardSessionHealthSnapshot",
    "DashboardRuntimeStateSnapshot",
    "build_dashboard_session_health",
    "build_dashboard_runtime_state",
    "KiteAuthDashboardSession",
    "serialize_kite_session",
]
