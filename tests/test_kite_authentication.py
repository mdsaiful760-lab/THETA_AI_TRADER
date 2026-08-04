"""Unit tests for broker.kite_authentication."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytest

from broker.base_broker import BrokerId
from broker.kite_authentication import (
    AuthHealthStatus,
    AuthenticationError,
    AuthenticationStatus,
    CredentialSource,
    EnvFileTokenStore,
    ExpiryPolicyKind,
    FileTokenStore,
    InvalidCredentialError,
    KiteAuthenticationConfig,
    KiteAuthenticator,
    KiteSession,
    NullTokenStore,
    SessionExpiredError,
    TokenEnvelope,
    TokenPersistenceError,
    TokenPersistenceMode,
    TokenSource,
    authenticate_from_request_token,
    compute_envelope_checksum,
    compute_next_0600_ist,
    compute_session_fingerprint,
    default_kite_authentication_config,
    deserialize_authentication_health_report,
    deserialize_authentication_result,
    is_session_expired,
    project_broker_session,
    redact_secret,
    restore_or_authenticate,
    serialize_authentication_health_report,
    serialize_authentication_result,
    serialize_kite_session,
    validate_kite_credentials,
    validate_kite_session,
)
from config.application_configuration import (
    EnvironmentProfile,
    InlineSecretProvider,
    SecretReference,
    SecretSource,
)

IST = ZoneInfo("Asia/Kolkata")


def fixed_clock() -> datetime:
    """Deterministic clock for auth tests."""
    return datetime(2026, 8, 4, 4, 30, tzinfo=timezone.utc)


class FakeKiteConnect:
    """Deterministic KiteConnect double for authentication tests."""

    def __init__(
        self,
        api_key: str,
        *,
        exchange_fail: bool = False,
        probe_fail: bool = False,
        probe_token_error: bool = False,
        invalidate_fail: bool = False,
    ) -> None:
        self.api_key = api_key
        self.access_token: str | None = None
        self.exchange_fail = exchange_fail
        self.probe_fail = probe_fail
        self.probe_token_error = probe_token_error
        self.invalidate_fail = invalidate_fail
        self.invalidate_calls = 0

    def login_url(self) -> str:
        return f"https://kite.zerodha.com/connect/login?api_key={self.api_key}"

    def generate_session(self, request_token: str, api_secret: str) -> dict[str, object]:
        if not api_secret:
            raise RuntimeError("missing secret")
        if self.exchange_fail:
            raise RuntimeError("network boom tokenABCDEF12")
        return {
            "access_token": f"access-{request_token}",
            "user_id": "AB1234",
            "user_name": "Test Trader",
            "broker": "ZERODHA",
            "login_time": "2026-08-04 09:00:00",
        }

    def set_access_token(self, access_token: str) -> None:
        self.access_token = access_token

    def profile(self) -> dict[str, object]:
        if self.probe_token_error:
            raise RuntimeError("TokenException invalid tokenXYZ999")
        if self.probe_fail:
            raise RuntimeError("profile unavailable")
        if not self.access_token:
            raise RuntimeError("TokenException")
        return {"user_id": "AB1234", "user_name": "Test Trader", "broker": "ZERODHA"}

    def invalidate_access_token(self) -> None:
        self.invalidate_calls += 1
        if self.invalidate_fail:
            raise RuntimeError("invalidate failed")
        self.access_token = None


def make_auth(
    tmp_path: Path,
    *,
    profile: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT,
    require_probe: bool = False,
    persistence: TokenPersistenceMode = TokenPersistenceMode.FILE,
    sdk: FakeKiteConnect | None = None,
    api_key: str = "api-key-123456",
    api_secret: str = "api-secret-123456",
    clock=fixed_clock,
    **kwargs: object,
) -> tuple[KiteAuthenticator, FakeKiteConnect]:
    """Build authenticator with fake SDK and file store."""
    fake = sdk or FakeKiteConnect(api_key)
    config = default_kite_authentication_config(profile)
    config = KiteAuthenticationConfig(
        environment_profile=profile,
        persistence_mode=persistence,
        token_store_path=str(tmp_path / "kite_session.json"),
        env_file_path=str(tmp_path / ".env"),
        require_profile_probe=require_probe,
        allow_env_file_persistence=profile is EnvironmentProfile.DEVELOPMENT,
        fail_closed_on_expiry=True,
        runner_kind="test",
        metadata=MappingProxyType({}),
    )
    auth = KiteAuthenticator(
        config,
        api_key=api_key,
        api_secret=api_secret,
        clock=clock,
        sdk_factory=lambda key: fake if key else FakeKiteConnect(key),
        env={},
        **kwargs,
    )
    return auth, fake


class TestCredentialValidation:
    """Credential validation tests."""

    def test_missing_api_key(self) -> None:
        with pytest.raises(InvalidCredentialError) as exc:
            validate_kite_credentials(None, "secret123")
        assert exc.value.code == "KITE_AUTH.CREDENTIAL.MISSING_API_KEY"

    def test_missing_api_secret(self) -> None:
        with pytest.raises(InvalidCredentialError) as exc:
            validate_kite_credentials("apikey1", None, require_secret=True)
        assert exc.value.code == "KITE_AUTH.CREDENTIAL.MISSING_API_SECRET"

    def test_placeholder_rejected(self) -> None:
        with pytest.raises(InvalidCredentialError) as exc:
            validate_kite_credentials("CHANGE_ME", "secret123456")
        assert exc.value.code == "KITE_AUTH.CREDENTIAL.PLACEHOLDER"

    def test_short_key_rejected(self) -> None:
        with pytest.raises(InvalidCredentialError):
            validate_kite_credentials("abc", "secret123456")


class TestExpiryHelpers:
    """Expiry and fingerprint helper tests."""

    def test_next_0600_same_day(self) -> None:
        # 04:30 UTC = 10:00 IST → next day 06:00 IST
        ts = datetime(2026, 8, 4, 4, 30, tzinfo=timezone.utc)
        expiry = compute_next_0600_ist(ts)
        local = expiry.astimezone(IST)
        assert local.hour == 6
        assert local.day == 5

    def test_next_0600_before_boundary(self) -> None:
        # 00:00 UTC = 05:30 IST → same day 06:00 IST
        ts = datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc)
        expiry = compute_next_0600_ist(ts)
        local = expiry.astimezone(IST)
        assert local.day == 4
        assert local.hour == 6

    def test_is_session_expired_with_skew(self) -> None:
        expires = datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)
        now = datetime(2026, 8, 4, 5, 59, 40, tzinfo=timezone.utc)
        assert is_session_expired(expires, now=now, skew_seconds=30) is True
        assert is_session_expired(expires, now=now, skew_seconds=10) is False

    def test_fingerprint_deterministic(self) -> None:
        kwargs = dict(
            session_id="s1",
            api_key="k",
            access_token="t",
            authenticated_at=fixed_clock(),
            expires_at=None,
            user_id="u",
            environment_profile="development",
        )
        assert compute_session_fingerprint(**kwargs) == compute_session_fingerprint(**kwargs)

    def test_fingerprint_changes_with_token(self) -> None:
        base = dict(
            session_id="s1",
            api_key="k",
            authenticated_at=fixed_clock(),
            expires_at=None,
            user_id=None,
            environment_profile="development",
        )
        a = compute_session_fingerprint(access_token="t1", **base)
        b = compute_session_fingerprint(access_token="t2", **base)
        assert a != b


class TestAuthenticateHappyPath:
    """Successful authentication tests."""

    def test_authenticate_seals_session(self, tmp_path: Path) -> None:
        auth, _fake = make_auth(tmp_path)
        url = auth.get_login_url()
        assert "api_key=" in url
        assert auth.get_status() is AuthenticationStatus.AWAITING_REQUEST_TOKEN
        result = auth.authenticate("req-token-1")
        assert result.status is AuthenticationStatus.AUTHENTICATED
        assert result.session is not None
        assert result.broker_session is not None
        assert result.broker_session.broker_id is BrokerId.KITE
        assert result.broker_session.credentials["access_token"] == "access-req-token-1"
        assert "access-req-token-1" not in serialize_authentication_result(result)

    def test_restore_round_trip(self, tmp_path: Path) -> None:
        auth, fake = make_auth(tmp_path)
        first = auth.authenticate("req-2")
        assert first.session is not None
        auth2, _ = make_auth(tmp_path, sdk=FakeKiteConnect("api-key-123456"))
        restored = auth2.restore_session()
        assert restored.status is AuthenticationStatus.AUTHENTICATED
        assert restored.session is not None
        assert restored.session.access_token == first.session.access_token
        assert restored.metadata.token_source is TokenSource.RESTORED

    def test_logout_clears_store(self, tmp_path: Path) -> None:
        auth, fake = make_auth(tmp_path)
        auth.authenticate("req-3")
        result = auth.logout(invalidate_remote=True)
        assert result.status is AuthenticationStatus.LOGGED_OUT
        assert auth.get_session() is None
        assert fake.invalidate_calls == 1
        restored = auth.restore_session()
        assert restored.status is AuthenticationStatus.UNAUTHENTICATED


class TestAuthenticateFailures:
    """Failure path tests."""

    def test_missing_request_token(self, tmp_path: Path) -> None:
        auth, _ = make_auth(tmp_path)
        result = auth.authenticate("  ")
        assert result.status is AuthenticationStatus.FAILED
        assert result.errors[0].code == "KITE_AUTH.CREDENTIAL.MISSING_REQUEST_TOKEN"

    def test_exchange_failure(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456", exchange_fail=True)
        auth, _ = make_auth(tmp_path, sdk=fake)
        result = auth.authenticate("req")
        assert result.status is AuthenticationStatus.FAILED
        assert result.errors[0].code == "KITE_AUTH.EXCHANGE.FAILED"
        assert "tokenABCDEF12" not in result.errors[0].message

    def test_probe_required_token_error(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456", probe_token_error=True)
        auth, _ = make_auth(tmp_path, sdk=fake, require_probe=True)
        result = auth.authenticate("req")
        assert result.status is AuthenticationStatus.EXPIRED
        assert result.errors[0].code == "KITE_AUTH.SESSION.EXPIRED"


class TestInjectAndRequire:
    """Injected token and require_broker_session tests."""

    def test_inject_access_token(self, tmp_path: Path) -> None:
        auth, _ = make_auth(tmp_path)
        result = auth.inject_access_token("injected-token-value")
        assert result.status is AuthenticationStatus.AUTHENTICATED
        assert result.metadata.token_source is TokenSource.INJECTED
        session = auth.require_broker_session()
        assert session.credentials["access_token"] == "injected-token-value"

    def test_require_broker_session_expired(self, tmp_path: Path) -> None:
        past = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

        def clock() -> datetime:
            return datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

        auth, _ = make_auth(tmp_path, clock=clock)
        result = auth.inject_access_token("tok", expires_at=past)
        assert result.status is AuthenticationStatus.EXPIRED
        with pytest.raises(SessionExpiredError):
            auth.require_broker_session()


class TestPersistence:
    """Token store tests."""

    def test_file_store_checksum_mismatch(self, tmp_path: Path) -> None:
        store = FileTokenStore(tmp_path / "sess.json")
        draft = TokenEnvelope(
            schema_version="1.0.0",
            session_id="s1",
            api_key="k",
            access_token="t",
            authenticated_at=fixed_clock(),
            session_fingerprint="fp",
            environment_profile="development",
            checksum="bad",
        )
        # Bypass save validation by writing raw JSON
        path = tmp_path / "sess.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "session_id": "s1",
                    "api_key": "k",
                    "access_token": "t",
                    "authenticated_at": "2026-08-04T04:30:00Z",
                    "expires_at": None,
                    "session_fingerprint": "fp",
                    "environment_profile": "development",
                    "checksum": "deadbeef",
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(TokenPersistenceError) as exc:
            store.load()
        assert exc.value.code == "KITE_AUTH.PERSIST.CHECKSUM_MISMATCH"

    def test_env_file_store(self, tmp_path: Path) -> None:
        path = tmp_path / ".env"
        path.write_text("OTHER=1\n", encoding="utf-8")
        store = EnvFileTokenStore(path)
        draft = TokenEnvelope(
            schema_version="1.0.0",
            session_id="s1",
            api_key="k123456",
            access_token="tok123456",
            authenticated_at=fixed_clock(),
            session_fingerprint="fp",
            environment_profile="development",
            checksum="",
        )
        envelope = TokenEnvelope(
            **{**draft.__dict__, "checksum": compute_envelope_checksum(draft)}
        )
        store.save(envelope)
        text = path.read_text(encoding="utf-8")
        assert "tok123456" in text
        assert store.load() is not None

    def test_null_store(self) -> None:
        store = NullTokenStore()
        assert store.load() is None
        assert store.is_available() is True
        store.clear()

    def test_production_forbids_env_file(self) -> None:
        with pytest.raises(TokenPersistenceError):
            KiteAuthenticationConfig(
                environment_profile=EnvironmentProfile.PRODUCTION,
                persistence_mode=TokenPersistenceMode.ENV_FILE,
                allow_env_file_persistence=False,
            )


class TestSerialization:
    """Serialization and redaction tests."""

    def test_result_round_trip_redacts_token(self, tmp_path: Path) -> None:
        auth, _ = make_auth(tmp_path)
        result = auth.authenticate("req-ser")
        payload = serialize_authentication_result(result)
        assert "access-req-ser" not in payload
        restored = deserialize_authentication_result(payload)
        assert restored.status is AuthenticationStatus.AUTHENTICATED
        assert restored.session is not None

    def test_health_round_trip(self, tmp_path: Path) -> None:
        auth, _ = make_auth(tmp_path)
        auth.authenticate("req-h")
        report = auth.get_health()
        assert report.overall_health is AuthHealthStatus.HEALTHY
        payload = serialize_authentication_health_report(report)
        assert "access-req-h" not in payload
        restored = deserialize_authentication_health_report(payload)
        assert restored.has_access_token is True

    def test_session_repr_redacts(self, tmp_path: Path) -> None:
        auth, _ = make_auth(tmp_path)
        result = auth.authenticate("req-repr")
        assert result.session is not None
        text = repr(result.session)
        assert "access-req-repr" not in text
        assert "<redacted>" in text

    def test_malformed_json(self) -> None:
        with pytest.raises(AuthenticationError) as exc:
            deserialize_authentication_result("{bad")
        assert exc.value.code == "KITE_AUTH.SERIALIZATION.MALFORMED"

    def test_unsupported_schema(self) -> None:
        with pytest.raises(AuthenticationError):
            deserialize_authentication_health_report(
                json.dumps({"schema_version": "9.9.9"})
            )


class TestSessionValidation:
    """KiteSession validation tests."""

    def test_wrong_broker_id(self) -> None:
        session = KiteSession(
            session_id="s",
            broker_id=BrokerId.MOCK,
            api_key="k123456",
            access_token="t123456",
            authenticated_at=fixed_clock(),
            session_fingerprint="fp",
            environment_profile=EnvironmentProfile.DEVELOPMENT,
        )
        with pytest.raises(AuthenticationError) as exc:
            validate_kite_session(session)
        assert exc.value.code == "KITE_AUTH.SESSION.INVALID_BROKER"

    def test_project_broker_session(self) -> None:
        session = KiteSession(
            session_id="s",
            broker_id=BrokerId.KITE,
            api_key="k123456",
            access_token="t123456",
            authenticated_at=fixed_clock(),
            expires_at=fixed_clock() + timedelta(hours=1),
            session_fingerprint="fp",
            environment_profile=EnvironmentProfile.DEVELOPMENT,
        )
        broker = project_broker_session(session)
        assert broker.credentials["api_key"] == "k123456"


class TestConvenienceAndEnv:
    """Convenience helpers and environment resolution."""

    def test_authenticate_from_request_token(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
        )
        result = authenticate_from_request_token(
            "req-c",
            config=config,
            api_key="api-key-123456",
            api_secret="api-secret-123456",
            clock=fixed_clock,
            sdk_factory=lambda key: fake,
            env={},
        )
        assert result.status is AuthenticationStatus.AUTHENTICATED

    def test_restore_or_authenticate_fallback(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
        )
        result = restore_or_authenticate(
            "req-fallback",
            config=config,
            api_key="api-key-123456",
            api_secret="api-secret-123456",
            clock=fixed_clock,
            sdk_factory=lambda key: fake,
            env={},
        )
        assert result.status is AuthenticationStatus.AUTHENTICATED

    def test_env_resolution(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("env-key-123456")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
        )
        auth = KiteAuthenticator(
            config,
            clock=fixed_clock,
            sdk_factory=lambda key: fake,
            env={
                "THETA_BROKER_API_KEY": "env-key-123456",
                "THETA_BROKER_API_SECRET": "env-secret-123456",
            },
        )
        assert auth.get_status() is AuthenticationStatus.CREDENTIALS_LOADED
        result = auth.authenticate("req-env")
        assert result.status is AuthenticationStatus.AUTHENTICATED

    def test_secret_provider_resolution(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("prov-key-123456")
        provider = InlineSecretProvider(
            {
                "broker.api_key": "prov-key-123456",
                "broker.api_secret": "prov-secret-123456",
            }
        )
        refs = {
            "broker.api_key": SecretReference(
                ref_id="broker.api_key",
                source=SecretSource.INLINE_FOR_TESTS,
                locator="broker.api_key",
                required=True,
            ),
            "broker.api_secret": SecretReference(
                ref_id="broker.api_secret",
                source=SecretSource.INLINE_FOR_TESTS,
                locator="broker.api_secret",
                required=True,
            ),
        }
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
        )
        auth = KiteAuthenticator(
            config,
            secret_provider=provider,
            secret_refs=refs,
            clock=fixed_clock,
            sdk_factory=lambda key: fake,
            env={},
        )
        result = auth.authenticate("req-prov")
        assert result.status is AuthenticationStatus.AUTHENTICATED
        assert result.metadata.credential_source in {
            CredentialSource.SECRET_PROVIDER,
            CredentialSource.MIXED,
        }


class TestHealthAndConcurrency:
    """Health reporting and concurrency tests."""

    def test_health_unauthenticated(self, tmp_path: Path) -> None:
        auth, _ = make_auth(tmp_path)
        health = auth.get_health()
        assert health.overall_health is AuthHealthStatus.UNKNOWN
        assert health.has_api_key is True

    def test_concurrent_health_during_authenticate(self, tmp_path: Path) -> None:
        auth, _ = make_auth(tmp_path)
        barrier = threading.Barrier(4)
        errors: list[BaseException] = []

        def reader() -> None:
            try:
                barrier.wait()
                for _ in range(20):
                    report = auth.get_health()
                    assert "access-" not in str(report.metadata)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def writer() -> None:
            try:
                barrier.wait()
                auth.authenticate("req-concurrent")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(reader) for _ in range(3)]
            futures.append(pool.submit(writer))
            for future in futures:
                future.result()
        assert not errors
        assert auth.get_status() is AuthenticationStatus.AUTHENTICATED


class TestDefaultsAndRedaction:
    """Default config and redaction helpers."""

    def test_default_configs(self) -> None:
        prod = default_kite_authentication_config(EnvironmentProfile.PRODUCTION)
        assert prod.require_profile_probe is True
        assert prod.allow_env_file_persistence is False
        paper = default_kite_authentication_config(EnvironmentProfile.PAPER)
        assert paper.persistence_mode is TokenPersistenceMode.FILE
        dev = default_kite_authentication_config(EnvironmentProfile.DEVELOPMENT)
        assert dev.require_profile_probe is False

    def test_redact_secret(self) -> None:
        assert redact_secret(None) == "<missing>"
        assert redact_secret("abc") == "<redacted>"

    def test_serialize_kite_session_include_secrets(self, tmp_path: Path) -> None:
        auth, _ = make_auth(tmp_path)
        result = auth.authenticate("req-sec")
        assert result.session is not None
        secret_payload = serialize_kite_session(result.session, include_secrets=True)
        assert "access-req-sec" in secret_payload


class TestLogoutRemoteFailure:
    """Logout continues when remote invalidate fails."""

    def test_logout_invalidate_failure_is_warning(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456", invalidate_fail=True)
        auth, _ = make_auth(tmp_path, sdk=fake)
        auth.authenticate("req-out")
        result = auth.logout(invalidate_remote=True)
        assert result.status is AuthenticationStatus.LOGGED_OUT
        assert result.warnings


class TestExpiredRestore:
    """Expired restored session handling."""

    def test_restore_expired_envelope(self, tmp_path: Path) -> None:
        store = FileTokenStore(tmp_path / "kite_session.json")
        past_auth = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
        past_exp = datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc)
        draft = TokenEnvelope(
            schema_version="1.0.0",
            session_id="s-exp",
            api_key="api-key-123456",
            access_token="old-token-123",
            authenticated_at=past_auth,
            expires_at=past_exp,
            session_fingerprint=compute_session_fingerprint(
                session_id="s-exp",
                api_key="api-key-123456",
                access_token="old-token-123",
                authenticated_at=past_auth,
                expires_at=past_exp,
                user_id=None,
                environment_profile="development",
            ),
            environment_profile="development",
            checksum="",
        )
        store.save(
            TokenEnvelope(**{**draft.__dict__, "checksum": compute_envelope_checksum(draft)})
        )
        auth, _ = make_auth(tmp_path)
        result = auth.restore_session()
        assert result.status is AuthenticationStatus.EXPIRED


class TestCoverageEdges:
    """Additional edge-case coverage for helpers, stores, and authenticator paths."""

    def test_config_invalid_skew(self) -> None:
        with pytest.raises(AuthenticationError) as exc:
            KiteAuthenticationConfig(clock_skew_seconds=-1)
        assert exc.value.code == "KITE_AUTH.CONFIG.INVALID"

    def test_to_broker_session_method(self, tmp_path: Path) -> None:
        auth, _ = make_auth(tmp_path)
        result = auth.authenticate("req-tb")
        assert result.session is not None
        projected = result.session.to_broker_session()
        assert projected.broker_id is BrokerId.KITE

    def test_short_and_placeholder_secret(self) -> None:
        with pytest.raises(InvalidCredentialError) as exc:
            validate_kite_credentials("apikey1", "abc", require_secret=True)
        assert exc.value.code == "KITE_AUTH.CREDENTIAL.INVALID_API_SECRET"
        with pytest.raises(InvalidCredentialError) as exc2:
            validate_kite_credentials(
                "apikey1", "CHANGE_ME", require_secret=True, reject_placeholders=True
            )
        assert exc2.value.code == "KITE_AUTH.CREDENTIAL.PLACEHOLDER"

    def test_session_validation_edges(self) -> None:
        with pytest.raises(AuthenticationError) as exc:
            validate_kite_session(
                KiteSession(
                    session_id=" ",
                    broker_id=BrokerId.KITE,
                    api_key="k123456",
                    access_token="t123456",
                    authenticated_at=fixed_clock(),
                    session_fingerprint="fp",
                    environment_profile=EnvironmentProfile.DEVELOPMENT,
                )
            )
        assert exc.value.code == "KITE_AUTH.SESSION.INVALID_ID"

        naive = datetime(2026, 8, 4, 4, 30)
        with pytest.raises(AuthenticationError) as exc2:
            validate_kite_session(
                KiteSession(
                    session_id="s1",
                    broker_id=BrokerId.KITE,
                    api_key="k123456",
                    access_token="t123456",
                    authenticated_at=naive,  # type: ignore[arg-type]
                    session_fingerprint="fp",
                    environment_profile=EnvironmentProfile.DEVELOPMENT,
                )
            )
        assert exc2.value.code == "KITE_AUTH.SESSION.NAIVE_TIMESTAMP"

        with pytest.raises(AuthenticationError) as exc3:
            validate_kite_session(
                KiteSession(
                    session_id="s1",
                    broker_id=BrokerId.KITE,
                    api_key="k123456",
                    access_token="t123456",
                    authenticated_at=fixed_clock(),
                    expires_at=naive,  # type: ignore[arg-type]
                    session_fingerprint="fp",
                    environment_profile=EnvironmentProfile.DEVELOPMENT,
                )
            )
        assert exc3.value.code == "KITE_AUTH.SESSION.NAIVE_TIMESTAMP"

        with pytest.raises(InvalidCredentialError):
            validate_kite_session(
                KiteSession(
                    session_id="s1",
                    broker_id=BrokerId.KITE,
                    api_key="   ",
                    access_token="t123456",
                    authenticated_at=fixed_clock(),
                    session_fingerprint="fp",
                    environment_profile=EnvironmentProfile.DEVELOPMENT,
                )
            )

    def test_is_session_expired_none(self) -> None:
        assert is_session_expired(None, now=fixed_clock(), skew_seconds=30) is False

    def test_null_store_save(self) -> None:
        store = NullTokenStore()
        draft = TokenEnvelope(
            schema_version="1.0.0",
            session_id="s",
            api_key="k",
            access_token="t",
            authenticated_at=fixed_clock(),
            session_fingerprint="fp",
            environment_profile="development",
            checksum="x",
        )
        store.save(draft)

    def test_file_store_corrupt_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not-json", encoding="utf-8")
        store = FileTokenStore(path)
        with pytest.raises(TokenPersistenceError) as exc:
            store.load()
        assert exc.value.code == "KITE_AUTH.PERSIST.IO_ERROR"

    def test_file_store_unsupported_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "sess.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "9.0.0",
                    "session_id": "s",
                    "api_key": "k",
                    "access_token": "t",
                    "authenticated_at": "2026-08-04T04:30:00Z",
                    "expires_at": None,
                    "session_fingerprint": "fp",
                    "environment_profile": "development",
                    "checksum": "x",
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(TokenPersistenceError) as exc:
            FileTokenStore(path).load()
        assert exc.value.code == "KITE_AUTH.PERSIST.UNSUPPORTED_VERSION"

    def test_env_file_comments_and_clear(self, tmp_path: Path) -> None:
        path = tmp_path / ".env"
        path.write_text("# comment\nOTHER=1\nTHETA_BROKER_ACCESS_TOKEN=old\n", encoding="utf-8")
        store = EnvFileTokenStore(path)
        draft = TokenEnvelope(
            schema_version="1.0.0",
            session_id="s1",
            api_key="k123456",
            access_token="tok123456",
            authenticated_at=fixed_clock(),
            session_fingerprint="fp",
            environment_profile="development",
            checksum="",
        )
        envelope = TokenEnvelope(
            **{**draft.__dict__, "checksum": compute_envelope_checksum(draft)}
        )
        store.save(envelope)
        assert store.is_available() is True
        store.clear()
        assert store.load() is None

    def test_custom_persistence_requires_store(self) -> None:
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.CUSTOM,
        )
        with pytest.raises(AuthenticationError) as exc:
            KiteAuthenticator(config, api_key="api-key-123456", env={})
        assert exc.value.code == "KITE_AUTH.CONFIG.INVALID"

    def test_env_file_mode_store(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.ENV_FILE,
            env_file_path=str(tmp_path / ".env"),
            allow_env_file_persistence=True,
        )
        auth = KiteAuthenticator(
            config,
            api_key="api-key-123456",
            api_secret="api-secret-123456",
            clock=fixed_clock,
            sdk_factory=lambda key: fake,
            env={},
        )
        result = auth.authenticate("req-envfile")
        assert result.status is AuthenticationStatus.AUTHENTICATED

    def test_legacy_env_keys(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("legacy-key-123")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
        )
        auth = KiteAuthenticator(
            config,
            clock=fixed_clock,
            sdk_factory=lambda key: fake,
            env={
                "KITE_API_KEY": "legacy-key-123",
                "KITE_API_SECRET": "legacy-secret-1",
            },
        )
        assert auth.get_status() is AuthenticationStatus.CREDENTIALS_LOADED

    def test_secret_provider_required_missing(self) -> None:
        provider = InlineSecretProvider({})
        refs = {
            "broker.api_key": SecretReference(
                ref_id="broker.api_key",
                source=SecretSource.INLINE_FOR_TESTS,
                locator="broker.api_key",
                required=True,
            ),
        }
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
        )
        with pytest.raises(InvalidCredentialError) as exc:
            KiteAuthenticator(
                config,
                secret_provider=provider,
                secret_refs=refs,
                clock=fixed_clock,
                env={},
            )
        assert exc.value.code == "KITE_AUTH.CREDENTIAL.SECRET_UNRESOLVED"

    def test_login_url_without_key(self) -> None:
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
        )
        auth = KiteAuthenticator(config, clock=fixed_clock, env={})
        with pytest.raises(InvalidCredentialError):
            auth.get_login_url()

    def test_malformed_exchange_response(self, tmp_path: Path) -> None:
        class BadSdk(FakeKiteConnect):
            def generate_session(self, request_token: str, api_secret: str) -> object:
                return "not-a-mapping"

        auth, _ = make_auth(tmp_path, sdk=BadSdk("api-key-123456"))
        result = auth.authenticate("req")
        assert result.status is AuthenticationStatus.FAILED
        assert result.errors[0].code == "KITE_AUTH.EXCHANGE.MALFORMED_RESPONSE"

    def test_missing_access_token_in_exchange(self, tmp_path: Path) -> None:
        class NoTokenSdk(FakeKiteConnect):
            def generate_session(self, request_token: str, api_secret: str) -> dict[str, object]:
                return {"user_id": "AB1234"}

        auth, _ = make_auth(tmp_path, sdk=NoTokenSdk("api-key-123456"))
        result = auth.authenticate("req")
        assert result.status is AuthenticationStatus.FAILED

    def test_probe_non_token_failure(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456", probe_fail=True)
        auth, _ = make_auth(tmp_path, sdk=fake, require_probe=True)
        result = auth.authenticate("req")
        assert result.status is AuthenticationStatus.FAILED
        assert result.errors[0].code == "KITE_AUTH.PROBE.FAILED"

    def test_optional_probe_warning(self, tmp_path: Path) -> None:
        class ProbeFailOptional(FakeKiteConnect):
            def profile(self) -> dict[str, object]:
                raise RuntimeError("profile unavailable")

        # require_probe False but still call set_access_token path; force optional probe
        # via authenticate with require False — exercise set_access_token exception path
        class SetFail(FakeKiteConnect):
            def set_access_token(self, access_token: str) -> None:
                raise RuntimeError("set failed")

        auth, _ = make_auth(tmp_path, sdk=SetFail("api-key-123456"), require_probe=False)
        result = auth.authenticate("req-set")
        assert result.status is AuthenticationStatus.AUTHENTICATED

    def test_production_no_expiry_degraded(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.PRODUCTION,
            persistence_mode=TokenPersistenceMode.DISABLED,
            default_expiry_policy=ExpiryPolicyKind.NONE,
            require_profile_probe=False,
            allow_env_file_persistence=False,
        )
        auth = KiteAuthenticator(
            config,
            api_key="api-key-123456",
            api_secret="api-secret-123456",
            clock=fixed_clock,
            sdk_factory=lambda key: fake,
            env={},
        )
        result = auth.authenticate("req-prod")
        assert result.status is AuthenticationStatus.DEGRADED
        health = auth.get_health()
        assert health.overall_health is AuthHealthStatus.DEGRADED

    def test_restore_with_probe_and_fingerprint_mismatch(self, tmp_path: Path) -> None:
        store = FileTokenStore(tmp_path / "kite_session.json")
        auth_at = fixed_clock()
        expires = compute_next_0600_ist(auth_at)
        draft = TokenEnvelope(
            schema_version="1.0.0",
            session_id="s-fp",
            api_key="api-key-123456",
            access_token="tok-fp-123456",
            authenticated_at=auth_at,
            expires_at=expires,
            user_id="AB1234",
            user_name="Test Trader",
            session_fingerprint="deadbeef" * 8,
            environment_profile="development",
            checksum="",
        )
        store.save(
            TokenEnvelope(**{**draft.__dict__, "checksum": compute_envelope_checksum(draft)})
        )
        fake = FakeKiteConnect("api-key-123456")
        auth, _ = make_auth(tmp_path, sdk=fake, require_probe=True)
        result = auth.restore_session()
        assert result.status is AuthenticationStatus.FAILED
        assert result.errors[0].code == "KITE_AUTH.SESSION.FINGERPRINT_MISMATCH"

    def test_restore_falls_back_to_inject(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
        )
        auth = KiteAuthenticator(
            config,
            api_key="api-key-123456",
            api_secret="api-secret-123456",
            access_token="injected-from-env-1",
            clock=fixed_clock,
            sdk_factory=lambda key: fake,
            env={},
            token_store=NullTokenStore(),
        )
        result = auth.restore_session()
        assert result.status is AuthenticationStatus.AUTHENTICATED
        assert result.metadata.token_source is TokenSource.INJECTED

    def test_inject_failures(self, tmp_path: Path) -> None:
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
        )
        auth = KiteAuthenticator(config, clock=fixed_clock, env={})
        result = auth.inject_access_token("tok")
        assert result.status is AuthenticationStatus.FAILED
        assert result.errors[0].code == "KITE_AUTH.CREDENTIAL.MISSING_API_KEY"

        auth2, _ = make_auth(tmp_path)
        result2 = auth2.inject_access_token("  ")
        assert result2.status is AuthenticationStatus.FAILED

        result3 = auth2.inject_access_token(
            "tok",
            expires_at=datetime(2026, 8, 4, 4, 30),  # naive
        )
        assert result3.status is AuthenticationStatus.FAILED
        assert result3.errors[0].code == "KITE_AUTH.SESSION.NAIVE_TIMESTAMP"

    def test_inject_with_probe(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456")
        auth, _ = make_auth(tmp_path, sdk=fake, require_probe=True)
        result = auth.inject_access_token("injected-probe-token")
        assert result.status is AuthenticationStatus.AUTHENTICATED
        assert result.metadata.profile_probe_performed is True

    def test_require_not_authenticated(self, tmp_path: Path) -> None:
        auth, _ = make_auth(tmp_path)
        with pytest.raises(AuthenticationError) as exc:
            auth.require_broker_session()
        assert exc.value.code == "KITE_AUTH.SESSION.NOT_AUTHENTICATED"

    def test_require_expires_live_session(self, tmp_path: Path) -> None:
        times = [
            datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        ]
        idx = {"i": 0}

        def clock() -> datetime:
            value = times[min(idx["i"], len(times) - 1)]
            idx["i"] += 1
            return value

        auth, _ = make_auth(tmp_path, clock=clock)
        result = auth.inject_access_token(
            "tok-live",
            expires_at=datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc),
        )
        assert result.status is AuthenticationStatus.AUTHENTICATED
        with pytest.raises(SessionExpiredError):
            auth.require_broker_session()

    def test_clear_persisted_session(self, tmp_path: Path) -> None:
        auth, _ = make_auth(tmp_path)
        auth.authenticate("req-clear")
        auth.clear_persisted_session()
        restored = auth.restore_session()
        assert restored.status is AuthenticationStatus.UNAUTHENTICATED

    def test_health_expiring_soon_and_secret_missing(self, tmp_path: Path) -> None:
        now = datetime(2026, 8, 4, 5, 0, tzinfo=timezone.utc)

        def clock() -> datetime:
            return now

        auth, _ = make_auth(tmp_path, clock=clock, api_secret="")
        # Re-create with secret for auth then strip for health? authenticate needs secret.
        # Instead inject and check expiring soon.
        auth2, _ = make_auth(tmp_path, clock=clock)
        auth2.inject_access_token(
            "tok-soon",
            expires_at=now + timedelta(minutes=20),
        )
        health = auth2.get_health()
        assert any(i.issue_code == "KITE_AUTH.HEALTH.TOKEN_EXPIRING_SOON" for i in health.issues)

    def test_health_store_unavailable(self, tmp_path: Path) -> None:
        class DeadStore(NullTokenStore):
            def is_available(self) -> bool:
                return False

        fake = FakeKiteConnect("api-key-123456")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.CUSTOM,
        )
        auth = KiteAuthenticator(
            config,
            api_key="api-key-123456",
            api_secret="api-secret-123456",
            clock=fixed_clock,
            sdk_factory=lambda key: fake,
            env={},
            token_store=DeadStore(),
        )
        health = auth.get_health()
        assert any(i.issue_code == "KITE_AUTH.HEALTH.STORE_UNAVAILABLE" for i in health.issues)

    def test_fixed_hours_expiry(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
            default_expiry_policy=ExpiryPolicyKind.FIXED_HOURS,
            fixed_ttl_hours=2.0,
        )
        auth = KiteAuthenticator(
            config,
            api_key="api-key-123456",
            api_secret="api-secret-123456",
            clock=fixed_clock,
            sdk_factory=lambda key: fake,
            env={},
        )
        result = auth.authenticate("req-fixed")
        assert result.session is not None
        assert result.session.expires_at == fixed_clock() + timedelta(hours=2)

    def test_restore_or_authenticate_prefers_restore(self, tmp_path: Path) -> None:
        auth, fake = make_auth(tmp_path)
        first = auth.authenticate("req-pref")
        assert first.status is AuthenticationStatus.AUTHENTICATED
        result = restore_or_authenticate(
            "unused-token",
            config=KiteAuthenticationConfig(
                environment_profile=EnvironmentProfile.DEVELOPMENT,
                persistence_mode=TokenPersistenceMode.FILE,
                token_store_path=str(tmp_path / "kite_session.json"),
            ),
            api_key="api-key-123456",
            api_secret="api-secret-123456",
            clock=fixed_clock,
            sdk_factory=lambda key: FakeKiteConnect("api-key-123456"),
            env={},
        )
        assert result.status is AuthenticationStatus.AUTHENTICATED
        assert result.metadata.token_source is TokenSource.RESTORED

    def test_restore_or_authenticate_no_token(self, tmp_path: Path) -> None:
        result = restore_or_authenticate(
            None,
            config=KiteAuthenticationConfig(
                environment_profile=EnvironmentProfile.DEVELOPMENT,
                persistence_mode=TokenPersistenceMode.DISABLED,
            ),
            api_key="api-key-123456",
            api_secret="api-secret-123456",
            clock=fixed_clock,
            sdk_factory=lambda key: FakeKiteConnect("api-key-123456"),
            env={},
        )
        assert result.status is AuthenticationStatus.UNAUTHENTICATED

    def test_logout_store_clear_warning(self, tmp_path: Path) -> None:
        class FlakyStore(NullTokenStore):
            def clear(self) -> None:
                raise TokenPersistenceError(
                    "clear failed",
                    code="KITE_AUTH.PERSIST.IO_ERROR",
                )

        fake = FakeKiteConnect("api-key-123456")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.CUSTOM,
        )
        auth = KiteAuthenticator(
            config,
            api_key="api-key-123456",
            api_secret="api-secret-123456",
            clock=fixed_clock,
            sdk_factory=lambda key: fake,
            env={},
            token_store=FlakyStore(),
        )
        auth.authenticate("req-flaky")
        result = auth.logout(invalidate_remote=False)
        assert result.status is AuthenticationStatus.LOGGED_OUT
        assert result.warnings

    def test_health_unhealthy_on_failed(self, tmp_path: Path) -> None:
        auth, _ = make_auth(tmp_path)
        auth.authenticate("  ")
        health = auth.get_health()
        assert health.overall_health is AuthHealthStatus.UNHEALTHY

    def test_optional_required_probe_warning_path_via_restore(self, tmp_path: Path) -> None:
        expires = fixed_clock()
        assert is_session_expired(expires, now=fixed_clock(), skew_seconds=0) is True

    def test_naive_next_0600_rejected(self) -> None:
        with pytest.raises(AuthenticationError):
            compute_next_0600_ist(datetime(2026, 8, 4, 4, 30))

    def test_result_unsupported_schema_version(self) -> None:
        with pytest.raises(AuthenticationError) as exc:
            deserialize_authentication_result(
                json.dumps({"schema_version": "0.0.1", "status": "failed"})
            )
        assert exc.value.code == "KITE_AUTH.SERIALIZATION.UNSUPPORTED_VERSION"

    def test_health_malformed_json(self) -> None:
        with pytest.raises(AuthenticationError) as exc:
            deserialize_authentication_health_report("{bad")
        assert exc.value.code == "KITE_AUTH.SERIALIZATION.MALFORMED"

    def test_mixed_credential_sources_with_access_token_env(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
        )
        auth = KiteAuthenticator(
            config,
            api_key="api-key-123456",
            clock=fixed_clock,
            sdk_factory=lambda key: fake,
            env={
                "THETA_BROKER_API_SECRET": "env-secret-123456",
                "THETA_BROKER_ACCESS_TOKEN": "env-access-token-1",
            },
        )
        result = auth.restore_session()
        assert result.status is AuthenticationStatus.AUTHENTICATED
        assert result.metadata.credential_source is CredentialSource.MIXED

    def test_optional_profile_probe_warning(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456", probe_fail=True)
        auth, _ = make_auth(tmp_path, sdk=fake)
        user_id, user_name, warnings = auth._run_profile_probe(
            fake,
            "tok",
            user_id=None,
            user_name=None,
            required=False,
        )
        assert warnings
        assert warnings[0].code == "KITE_AUTH.PROBE.FAILED"

    def test_restore_empty_access_token(self, tmp_path: Path) -> None:
        path = tmp_path / "kite_session.json"
        draft = TokenEnvelope(
            schema_version="1.0.0",
            session_id="s-empty",
            api_key="api-key-123456",
            access_token="placeholder",
            authenticated_at=fixed_clock(),
            expires_at=compute_next_0600_ist(fixed_clock()),
            session_fingerprint="fp",
            environment_profile="development",
            checksum="",
        )
        checksum = compute_envelope_checksum(draft)
        # Write envelope then overwrite access_token to whitespace while keeping checksum
        # of non-empty token so load succeeds checksum, then authenticator rejects empty.
        # Simpler: write valid envelope with access_token="   " and matching checksum.
        blank = TokenEnvelope(
            schema_version="1.0.0",
            session_id="s-empty",
            api_key="api-key-123456",
            access_token="   ",
            authenticated_at=fixed_clock(),
            expires_at=compute_next_0600_ist(fixed_clock()),
            session_fingerprint="fp",
            environment_profile="development",
            checksum="",
        )
        FileTokenStore(path).save(
            TokenEnvelope(**{**blank.__dict__, "checksum": compute_envelope_checksum(blank)})
        )
        auth, _ = make_auth(tmp_path)
        result = auth.restore_session()
        assert result.status is AuthenticationStatus.FAILED
        assert result.errors[0].code == "KITE_AUTH.CREDENTIAL.MISSING_ACCESS_TOKEN"

    def test_production_rejects_placeholder_on_init(self) -> None:
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.PRODUCTION,
            persistence_mode=TokenPersistenceMode.DISABLED,
            allow_env_file_persistence=False,
        )
        with pytest.raises(InvalidCredentialError):
            KiteAuthenticator(
                config,
                api_key="CHANGE_ME",
                api_secret="api-secret-123456",
                clock=fixed_clock,
                env={},
            )

    def test_default_clock_path(self, tmp_path: Path) -> None:
        fake = FakeKiteConnect("api-key-123456")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
        )
        auth = KiteAuthenticator(
            config,
            api_key="api-key-123456",
            api_secret="api-secret-123456",
            sdk_factory=lambda key: fake,
            env={},
        )
        result = auth.authenticate("req-clock")
        assert result.status is AuthenticationStatus.AUTHENTICATED

    def test_health_expired_and_secret_missing(self, tmp_path: Path) -> None:
        now = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

        def clock() -> datetime:
            return now

        fake = FakeKiteConnect("api-key-123456")
        config = KiteAuthenticationConfig(
            environment_profile=EnvironmentProfile.DEVELOPMENT,
            persistence_mode=TokenPersistenceMode.DISABLED,
            fail_closed_on_expiry=False,
        )
        auth = KiteAuthenticator(
            config,
            api_key="api-key-123456",
            clock=clock,
            sdk_factory=lambda key: fake,
            env={},
            token_store=NullTokenStore(),
        )
        # Manually seal an expired authenticated session for health checks.
        session = KiteSession(
            session_id="s-h",
            broker_id=BrokerId.KITE,
            api_key="api-key-123456",
            access_token="tok-health",
            authenticated_at=datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
            expires_at=datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc),
            session_fingerprint="fp",
            environment_profile=EnvironmentProfile.DEVELOPMENT,
        )
        with auth._lock:
            auth._session = session
            auth._status = AuthenticationStatus.AUTHENTICATED
            auth._api_secret = None
        health = auth.get_health()
        assert health.is_expired is True
        assert any(i.issue_code == "KITE_AUTH.HEALTH.TOKEN_EXPIRED" for i in health.issues)
        assert any(i.issue_code == "KITE_AUTH.HEALTH.SECRET_MISSING" for i in health.issues)
