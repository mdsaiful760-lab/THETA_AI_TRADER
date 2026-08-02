"""Kite SDK exception translation."""

from __future__ import annotations

from broker.base_broker import (
    ERROR_AUTH_EXPIRED,
    ERROR_AUTH_INVALID,
    ERROR_CONNECTION_FAILED,
    ERROR_INTERNAL_UNHANDLED,
    ERROR_ORDER_REJECTED,
    ERROR_REQUEST_INVALID,
    BrokerAuthenticationError,
    BrokerClientError,
    BrokerConnectionError,
    BrokerId,
    BrokerOrderError,
    BrokerRequestError,
)


def map_kite_exception(
    exc: BaseException,
    *,
    broker_id: BrokerId = BrokerId.KITE,
) -> BrokerClientError:
    """Translate a Kite SDK exception into a broker client error.

    Args:
        exc: Exception raised by kiteconnect.
        broker_id: Originating broker identifier.

    Returns:
        Mapped broker client error instance.
    """
    if isinstance(exc, BrokerClientError):
        return exc

    module_name = type(exc).__module__
    class_name = type(exc).__name__
    message = _sanitize_message(str(exc))

    if module_name.startswith("kiteconnect"):
        if class_name == "TokenException":
            return BrokerAuthenticationError(
                message,
                code=ERROR_AUTH_EXPIRED,
                broker_id=broker_id,
                cause=exc,
            )
        if class_name == "PermissionException":
            return BrokerAuthenticationError(
                message,
                code=ERROR_AUTH_INVALID,
                broker_id=broker_id,
                cause=exc,
            )
        if class_name == "NetworkException":
            return BrokerConnectionError(
                message,
                code=ERROR_CONNECTION_FAILED,
                broker_id=broker_id,
                cause=exc,
            )
        if class_name in {"DataException", "InputException"}:
            return BrokerRequestError(
                message,
                code=ERROR_REQUEST_INVALID,
                broker_id=broker_id,
                cause=exc,
            )
        if class_name == "OrderException":
            return BrokerOrderError(
                message,
                code=ERROR_ORDER_REJECTED,
                broker_id=broker_id,
                cause=exc,
            )

    return BrokerClientError(
        message,
        code=ERROR_INTERNAL_UNHANDLED,
        recoverable=False,
        broker_id=broker_id,
        cause=exc,
    )


def _sanitize_message(message: str) -> str:
    """Remove token-like substrings from error messages."""
    lowered = message.lower()
    if "access_token" in lowered or "api_key" in lowered:
        return "kite request failed"
    return message
