"""Configuration policy for KiteBrokerClient."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


KITE_BROKER_VERSION: str = "1.0.0"


class KiteWebSocketTickMode(str, Enum):
    """WebSocket tick subscription mode."""

    FULL = "full"
    QUOTE = "quote"


@dataclass(frozen=True)
class KiteBrokerPolicy:
    """Runtime policy for Kite broker transport behaviour."""

    rate_limit_rps: float = 3.0
    rate_limit_wait_seconds: float = 0.5
    quote_batch_size: int = 100
    max_subscribed_tokens: int = 3000
    ws_tick_mode: KiteWebSocketTickMode = KiteWebSocketTickMode.FULL
    retry_max_attempts: int = 3
    retry_initial_delay_ms: int = 100
    retry_max_delay_ms: int = 2000
    retry_jitter: bool = True
    rest_timeout_seconds: float = 2.0
    enable_margin_preview: bool = False
    enable_holdings: bool = True
    enable_ohlc_batch: bool = False
    enable_funds_breakdown: bool = False
