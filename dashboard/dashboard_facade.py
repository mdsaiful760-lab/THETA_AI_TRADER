"""Read-only dashboard integration facade for backend snapshot aggregation."""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from dashboard.facade import DashboardBackendFacade
from dashboard.view_models import (
    AnalyticsPageView,
    ApmeDecisionView,
    ApmePageView,
    FacadeActionResult,
    HomeKpiView,
    HomePageView,
    IndexQuoteView,
    LogEntryView,
    LogsPageView,
    MarketPageView,
    OrderRowView,
    OrdersPageView,
    PaperPositionView,
    PaperTradingPageView,
    PortfolioPageView,
    PortfolioPositionView,
    RiskPageView,
    RuntimeStateView,
    SettingsPageView,
    StrategyMonitorView,
    StrategyRowView,
    SystemStatusView,
    default_index_quotes,
)

DASHBOARD_FACADE_SCHEMA_VERSION: str = "1.0.0"

HOME_MARKET_INDEX_SYMBOLS: tuple[str, ...] = (
    "NIFTY",
    "BANKNIFTY",
    "SENSEX",
    "INDIA VIX",
)

# Canonical Strategy Monitor families: (family_id, display_name)
STRATEGY_MONITOR_FAMILIES: tuple[tuple[str, str], ...] = (
    ("short_strangle", "Short Strangle"),
    ("iron_condor", "Iron Condor"),
    ("bull_put_spread", "Bull Put Spread"),
    ("bear_call_spread", "Bear Call Spread"),
)

PLACEHOLDER: str = "—"


DEFAULT_REDACT_SECRET_KEYS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "access_key",
    "auth",
)

MAX_LOG_MESSAGE_LENGTH: int = 2_000

_SECRET_PATTERN = re.compile(
    r"(?i)(token|secret|password|api[_-]?key|access[_-]?key|auth)"
    r"(\s*[:=]\s*)(\S+)",
)


class DashboardFacadeError(Exception):
    """Base exception for dashboard integration facade errors."""


class DashboardFacadeConfigurationError(DashboardFacadeError):
    """Raised when facade configuration is invalid."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize with stable error code and message.

        Args:
            code: Stable configuration error code (e.g. ``CFG-DIF-001``).
            message: Human-readable validation failure description.
        """
        super().__init__(message)
        self.code = code
        self.message = message


class DashboardFacadeValidationError(DashboardFacadeError):
    """Raised when a facade DTO invariant fails validation."""


@dataclass(frozen=True)
class DashboardIntegrationFacadeConfig:
    """Immutable configuration for :class:`DashboardIntegrationFacade`.

    Attributes:
        schema_version: Facade DTO schema version; must equal
            ``DASHBOARD_FACADE_SCHEMA_VERSION``.
        cache_ttl_seconds: TTL for optional getter memoization; ``0`` disables cache.
        log_limit_default: Default maximum log entries returned by ``get_logs``.
        placeholder: Display placeholder for unknown numeric or text values.
        redact_secret_keys: Lowercase key fragments used for secret redaction.
        enable_lifecycle_passthrough: When ``True``, ``start``/``stop`` delegate
            to the injected session.
        metadata: Non-secret audit metadata.
    """

    schema_version: str = DASHBOARD_FACADE_SCHEMA_VERSION
    cache_ttl_seconds: float = 0.0
    log_limit_default: int = 200
    placeholder: str = "—"
    redact_secret_keys: tuple[str, ...] = DEFAULT_REDACT_SECRET_KEYS
    enable_lifecycle_passthrough: bool = False
    metadata: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        """Validate configuration invariants."""
        if self.schema_version != DASHBOARD_FACADE_SCHEMA_VERSION:
            raise DashboardFacadeConfigurationError(
                "CFG-DIF-001",
                f"schema_version must be {DASHBOARD_FACADE_SCHEMA_VERSION!r}, "
                f"got {self.schema_version!r}",
            )
        if self.cache_ttl_seconds < 0:
            raise DashboardFacadeConfigurationError(
                "CFG-DIF-002",
                "cache_ttl_seconds must be >= 0",
            )
        if self.log_limit_default < 1:
            raise DashboardFacadeConfigurationError(
                "CFG-DIF-003",
                "log_limit_default must be >= 1",
            )
        if not self.placeholder:
            raise DashboardFacadeConfigurationError(
                "CFG-DIF-004",
                "placeholder must be non-empty",
            )


@dataclass(frozen=True)
class FacadeStrategyRow:
    """Strategy monitor row for facade consumers."""

    strategy_id: str
    family: str
    status: str
    confidence: str
    last_signal: str
    timestamp: str
    reasons: tuple[str, ...] = ()
    score: str = PLACEHOLDER
    eligibility: str = PLACEHOLDER
    reason: str = PLACEHOLDER
    display_name: str = PLACEHOLDER


@dataclass(frozen=True)
class FacadePaperPositionRow:
    """Paper trading position row."""

    symbol: str
    quantity: str
    avg_price: str
    mark: str
    pnl: str


@dataclass(frozen=True)
class FacadeOrderRow:
    """Order summary row."""

    order_id: str
    plan_id: str
    status: str
    symbol: str
    side: str
    quantity: str
    timestamp: str


@dataclass(frozen=True)
class FacadePortfolioPositionRow:
    """Portfolio position row."""

    symbol: str
    quantity: str
    exposure: str
    pnl: str


@dataclass(frozen=True)
class FacadeApmeDecisionRow:
    """APME decision summary row."""

    position_id: str
    action: str
    rationale: str
    timestamp: str


@dataclass(frozen=True)
class FacadeLogEntry:
    """Single redacted log line."""

    timestamp: str
    level: str
    message: str
    logger: str


@dataclass(frozen=True)
class FacadeSystemStatus:
    """Aggregated system, broker, mode, and market status."""

    schema_version: str
    as_of: datetime
    source: str
    system_status: str
    broker_status: str
    execution_mode: str
    market_status: str
    message: str
    facade_healthy: bool


@dataclass(frozen=True)
class FacadeMarketSnapshot:
    """Market display snapshot."""

    schema_version: str
    as_of: datetime
    source: str
    underlyings: tuple[str, ...]
    selected_underlying: str
    ltp: str
    change: str
    volume: str
    option_chain_columns: tuple[str, ...]
    option_chain_rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class HomeIndexQuote:
    """Single Home terminal index quote for display."""

    symbol: str
    ltp: str = PLACEHOLDER
    change_abs: str = PLACEHOLDER
    change_pct: str = PLACEHOLDER
    last_update: str = PLACEHOLDER
    connection_status: str = "UNKNOWN"


@dataclass(frozen=True)
class FacadeHomeMarketIndices:
    """Four Home terminal index quotes aggregation payload."""

    indices: tuple[HomeIndexQuote, ...]
    as_of: datetime
    source: str
    market_status: str
    facade_connected: bool
    schema_version: str = DASHBOARD_FACADE_SCHEMA_VERSION


@dataclass(frozen=True)
class FacadeStrategyStatus:
    """Strategy evaluation status snapshot for Strategy Monitor."""

    schema_version: str
    as_of: datetime
    source: str
    strategies: tuple[FacadeStrategyRow, ...]
    market_regime: str = PLACEHOLDER
    active_strategy: str = PLACEHOLDER
    confidence_score: str = PLACEHOLDER
    evaluation_time: str = PLACEHOLDER


@dataclass(frozen=True)
class FacadePaperPositions:
    """Paper capital and position display snapshot."""

    schema_version: str
    as_of: datetime
    source: str
    virtual_cash: str
    realized_pnl: str
    unrealized_pnl: str
    positions: tuple[FacadePaperPositionRow, ...]
    equity_series: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class FacadePaperLedgerPosition:
    """Paper Trading page position row."""

    symbol: str
    strategy: str = PLACEHOLDER
    quantity: str = PLACEHOLDER
    entry: str = PLACEHOLDER
    current: str = PLACEHOLDER
    mtm: str = PLACEHOLDER
    status: str = PLACEHOLDER


@dataclass(frozen=True)
class FacadePaperTradingLedger:
    """Paper Trading page ledger aggregation payload."""

    schema_version: str
    as_of: datetime
    source: str
    available_cash: str
    capital_used: str
    total_equity: str
    todays_pnl: str
    realized_pnl: str
    unrealized_pnl: str
    positions: tuple[FacadePaperLedgerPosition, ...]
    orders_filled: str
    orders_pending: str
    orders_cancelled: str
    orders_rejected: str
    orders: tuple[FacadeOrderRow, ...] = ()
    equity_series: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class FacadeOrderBook:
    """Recent order summary rows."""

    schema_version: str
    as_of: datetime
    source: str
    orders: tuple[FacadeOrderRow, ...]


@dataclass(frozen=True)
class FacadePortfolio:
    """Portfolio metrics and position rows."""

    schema_version: str
    as_of: datetime
    source: str
    equity: str
    exposure: str
    utilization: str
    positions: tuple[FacadePortfolioPositionRow, ...]
    equity_series: tuple[tuple[str, float], ...]
    allocation_series: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class FacadeRisk:
    """Last risk verdict summary."""

    schema_version: str
    as_of: datetime
    source: str
    verdict: str
    reason_codes: tuple[str, ...]
    limits: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class FacadeApme:
    """Informational APME decision summaries."""

    schema_version: str
    as_of: datetime
    source: str
    summary: str
    decisions: tuple[FacadeApmeDecisionRow, ...]


@dataclass(frozen=True)
class FacadeLogs:
    """Bounded, redacted log entries (newest-first)."""

    schema_version: str
    as_of: datetime
    source: str
    entries: tuple[FacadeLogEntry, ...]
    limit: int


@dataclass(frozen=True)
class FacadePerformance:
    """Performance and analytics aggregates."""

    schema_version: str
    as_of: datetime
    source: str
    metrics: tuple[tuple[str, str], ...]
    series: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class FacadeHealthReport:
    """Facade-local health report."""

    schema_version: str
    connected: bool
    status: str
    cache_entries: int
    last_refresh_at: datetime | None
    last_error_code: str | None


@dataclass(frozen=True)
class FacadeRefreshResult:
    """Result of a cache invalidation and snapshot re-read."""

    schema_version: str
    refreshed_at: datetime
    cache_cleared: bool
    success: bool
    message: str


@runtime_checkable
class IntegrationSessionLike(Protocol):
    """Structural protocol for optional upstream integration session reads."""

    def get_health(self) -> object:
        """Return integration health snapshot."""

    def get_runtime_state(self) -> object:
        """Return runtime execution state snapshot."""


def _utc_now() -> datetime:
    """Return current UTC time with timezone info."""
    return datetime.now(timezone.utc)


def _meta(
    *,
    as_of: datetime,
    source: str,
) -> dict[str, Any]:
    """Build common DTO metadata fields."""
    return {
        "schema_version": DASHBOARD_FACADE_SCHEMA_VERSION,
        "as_of": as_of,
        "source": source,
    }


def empty_system_status(
    *,
    as_of: datetime | None = None,
    placeholder: str = "—",
    facade_healthy: bool = True,
) -> FacadeSystemStatus:
    """Return offline/disconnected system status placeholders."""
    ts = as_of or _utc_now()
    return FacadeSystemStatus(
        **_meta(as_of=ts, source="offline"),
        system_status="DISCONNECTED",
        broker_status="N/A",
        execution_mode="ANALYSIS",
        market_status="UNKNOWN",
        message=placeholder if placeholder != "—" else "Backend unavailable",
        facade_healthy=facade_healthy,
    )


def empty_market_snapshot(
    *,
    as_of: datetime | None = None,
    placeholder: str = "—",
) -> FacadeMarketSnapshot:
    """Return empty market snapshot with placeholders."""
    ts = as_of or _utc_now()
    return FacadeMarketSnapshot(
        **_meta(as_of=ts, source="offline"),
        underlyings=(),
        selected_underlying=placeholder,
        ltp=placeholder,
        change=placeholder,
        volume=placeholder,
        option_chain_columns=("strike", "type", "ltp", "oi", "iv"),
        option_chain_rows=(),
    )


def empty_home_market_indices(
    *,
    as_of: datetime | None = None,
    placeholder: str = PLACEHOLDER,
    market_status: str = "UNKNOWN",
    facade_connected: bool = False,
    source: str = "offline",
    connection_status: str = "OFFLINE",
) -> FacadeHomeMarketIndices:
    """Return offline placeholder Home index quotes for all four symbols.

    Args:
        as_of: Snapshot timestamp.
        placeholder: Display placeholder for missing numeric fields.
        market_status: Market status label.
        facade_connected: Whether a live session is attached.
        source: Payload source tag.
        connection_status: Per-index connection status.

    Returns:
        Ordered four-symbol ``FacadeHomeMarketIndices`` with placeholders.
    """
    ts = as_of or _utc_now()
    indices = tuple(
        HomeIndexQuote(
            symbol=symbol,
            ltp=placeholder,
            change_abs=placeholder,
            change_pct=placeholder,
            last_update=placeholder,
            connection_status=connection_status,
        )
        for symbol in HOME_MARKET_INDEX_SYMBOLS
    )
    return FacadeHomeMarketIndices(
        indices=indices,
        as_of=ts,
        source=source,
        market_status=market_status,
        facade_connected=facade_connected,
        schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
    )


def _field(obj: object, *names: str, default: object = None) -> object:
    """Read the first available attribute or mapping key from ``names``."""
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _format_ltp(value: object | None, placeholder: str) -> str:
    """Format an LTP value for display."""
    if value is None:
        return placeholder
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _display_str(value, placeholder)
    return f"{number:,.2f}"


def _format_change_abs(value: object | None, placeholder: str) -> str:
    """Format absolute change with an explicit sign when numeric."""
    if value is None:
        return placeholder
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _display_str(value, placeholder)
    return f"{number:+,.2f}"


def _format_change_pct(value: object | None, placeholder: str) -> str:
    """Format percentage change with an explicit sign when numeric."""
    if value is None:
        return placeholder
    try:
        number = float(value)
    except (TypeError, ValueError):
        text = _display_str(value, placeholder)
        if text != placeholder and not text.endswith("%"):
            return f"{text}%"
        return text
    return f"{number:+.2f}%"


def _format_timestamp(value: object | None, placeholder: str) -> str:
    """Format a last-update timestamp for display."""
    if value is None:
        return placeholder
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S UTC")
    return _display_str(value, placeholder)


def _format_score(value: object | None, placeholder: str) -> str:
    """Format a strategy score for display."""
    if value is None:
        return placeholder
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _display_str(value, placeholder)
    return f"{number:.2f}"


def _format_confidence(value: object | None, placeholder: str) -> str:
    """Format a confidence score for display."""
    if value is None:
        return placeholder
    if hasattr(value, "overall_score"):
        return _format_score(getattr(value, "overall_score"), placeholder)
    if hasattr(value, "score"):
        return _format_score(getattr(value, "score"), placeholder)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _display_str(value, placeholder)
    if 0.0 <= number <= 1.0:
        return f"{number * 100.0:.1f}%"
    return f"{number:.2f}"


def _format_eligibility(item: object, placeholder: str) -> str:
    """Map upstream eligibility/status fields to Eligible / Rejected labels.

    Display-only mapping of already-computed upstream values — does not decide
    trade eligibility.
    """
    explicit = _field(item, "eligibility", "eligible_label", "eligibility_label")
    if explicit is not None:
        text = _display_str(explicit, placeholder)
        if text != placeholder:
            lowered = text.lower()
            if lowered in {"eligible", "true", "yes", "1"}:
                return "Eligible"
            if lowered in {"rejected", "ineligible", "false", "no", "0"}:
                return "Rejected"
            if text in {"Eligible", "Rejected"}:
                return text
            return text

    eligible = _field(item, "eligible", "is_eligible")
    if eligible is True:
        return "Eligible"
    if eligible is False:
        return "Rejected"

    outcome = _display_str(
        _field(item, "outcome_class", "evaluation_outcome"),
        "",
    ).lower()
    if outcome in {"actionable"}:
        return "Eligible"
    if outcome in {"no_trade", "error", "monitor"}:
        return "Rejected"

    status = _display_str(
        _field(item, "status", "evaluation_status"),
        "",
    ).lower()
    if status in {"eligible", "active", "success", "selected", "actionable"}:
        return "Eligible"
    if status in {
        "rejected",
        "abstain",
        "failed",
        "skipped",
        "timeout",
        "no_trade",
        "error",
        "ineligible",
    }:
        return "Rejected"
    return placeholder


def _normalize_family_id(value: object | None) -> str:
    """Normalize a strategy family identifier to snake_case id."""
    if value is None:
        return ""
    raw = str(value).strip()
    if hasattr(value, "value"):
        raw = str(getattr(value, "value")).strip() or raw
    lowered = raw.lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "shortstrangle": "short_strangle",
        "ironcondor": "iron_condor",
        "bullputspread": "bull_put_spread",
        "bearcallspread": "bear_call_spread",
    }
    compact = lowered.replace("_", "")
    if compact in aliases:
        return aliases[compact]
    return lowered


def _primary_reason(item: object, placeholder: str) -> tuple[str, tuple[str, ...]]:
    """Extract primary reason string and reasons tuple from an upstream row."""
    reasons = _tuple_str(
        _field(item, "reasons", "reason_codes", "reason_list", default=())
    )
    single = _field(item, "reason")
    if single is not None:
        text = _display_str(single, placeholder)
        if text != placeholder:
            if text not in reasons:
                reasons = (text, *reasons)
            return text, reasons
    if reasons:
        return reasons[0], reasons
    return placeholder, ()


def home_indices_to_quote_views(
    payload: FacadeHomeMarketIndices,
) -> tuple[IndexQuoteView, ...]:
    """Map facade home indices to presentation ``IndexQuoteView`` rows.

    Args:
        payload: Facade home market indices DTO.

    Returns:
        Presentation index quote views for the Home strip.
    """
    views: list[IndexQuoteView] = []
    for quote in payload.indices:
        compact = PLACEHOLDER
        if quote.change_abs != PLACEHOLDER and quote.change_pct != PLACEHOLDER:
            compact = f"{quote.change_abs} ({quote.change_pct})"
        elif quote.change_abs != PLACEHOLDER:
            compact = quote.change_abs
        elif quote.change_pct != PLACEHOLDER:
            compact = quote.change_pct
        views.append(
            IndexQuoteView(
                symbol=quote.symbol,
                value=quote.ltp,
                change=compact,
                change_abs=quote.change_abs,
                change_pct=quote.change_pct,
                last_update=quote.last_update,
                status=quote.connection_status,
                connection_status=quote.connection_status,
            )
        )
    return tuple(views)


def empty_strategy_status(
    *,
    as_of: datetime | None = None,
    placeholder: str = PLACEHOLDER,
    source: str = "offline",
) -> FacadeStrategyStatus:
    """Return Strategy Monitor placeholder snapshot with four strategy rows.

    Args:
        as_of: Snapshot timestamp.
        placeholder: Display placeholder for missing fields.
        source: Payload source tag.

    Returns:
        Ordered four-family ``FacadeStrategyStatus`` with placeholders.
    """
    ts = as_of or _utc_now()
    strategies = tuple(
        FacadeStrategyRow(
            strategy_id=family_id,
            family=family_id,
            display_name=display_name,
            status=placeholder,
            confidence=placeholder,
            last_signal=placeholder,
            timestamp=placeholder,
            reasons=(),
            score=placeholder,
            eligibility=placeholder,
            reason=placeholder,
        )
        for family_id, display_name in STRATEGY_MONITOR_FAMILIES
    )
    return FacadeStrategyStatus(
        **_meta(as_of=ts, source=source),
        strategies=strategies,
        market_regime=placeholder,
        active_strategy=placeholder,
        confidence_score=placeholder,
        evaluation_time=placeholder,
    )


def strategy_status_to_monitor_view(
    payload: FacadeStrategyStatus,
) -> StrategyMonitorView:
    """Map facade strategy status to presentation ``StrategyMonitorView``.

    Args:
        payload: Facade strategy status DTO.

    Returns:
        Presentation view for the Strategy Monitor page.
    """
    return StrategyMonitorView(
        market_regime=payload.market_regime,
        active_strategy=payload.active_strategy,
        confidence_score=payload.confidence_score,
        evaluation_time=payload.evaluation_time,
        strategies=tuple(
            StrategyRowView(
                strategy_id=row.strategy_id,
                family=row.family,
                display_name=row.display_name
                if row.display_name != PLACEHOLDER
                else row.family,
                status=row.status,
                confidence=row.confidence,
                last_signal=row.last_signal,
                timestamp=row.timestamp,
                reasons=row.reasons,
                score=row.score,
                eligibility=row.eligibility,
                reason=row.reason
                if row.reason != PLACEHOLDER
                else (", ".join(row.reasons) if row.reasons else PLACEHOLDER),
            )
            for row in payload.strategies
        ),
        source=payload.source,
        as_of=payload.as_of.isoformat()
        if isinstance(payload.as_of, datetime)
        else PLACEHOLDER,
    )


def empty_paper_positions(
    *,
    as_of: datetime | None = None,
    placeholder: str = "—",
) -> FacadePaperPositions:
    """Return empty paper positions snapshot."""
    ts = as_of or _utc_now()
    return FacadePaperPositions(
        **_meta(as_of=ts, source="offline"),
        virtual_cash=placeholder,
        realized_pnl=placeholder,
        unrealized_pnl=placeholder,
        positions=(),
        equity_series=(),
    )


def empty_paper_trading_ledger(
    *,
    as_of: datetime | None = None,
    placeholder: str = PLACEHOLDER,
    source: str = "offline",
) -> FacadePaperTradingLedger:
    """Return offline Paper Trading ledger placeholders.

    Args:
        as_of: Snapshot timestamp.
        placeholder: Display placeholder for missing money fields.
        source: Payload source tag.

    Returns:
        Empty ledger with money KPIs as placeholders and order counts ``0``.
    """
    ts = as_of or _utc_now()
    return FacadePaperTradingLedger(
        **_meta(as_of=ts, source=source),
        available_cash=placeholder,
        capital_used=placeholder,
        total_equity=placeholder,
        todays_pnl=placeholder,
        realized_pnl=placeholder,
        unrealized_pnl=placeholder,
        positions=(),
        orders_filled="0",
        orders_pending="0",
        orders_cancelled="0",
        orders_rejected="0",
        orders=(),
        equity_series=(),
    )


def _format_money(value: object | None, placeholder: str) -> str:
    """Format a money-like value for display."""
    if value is None:
        return placeholder
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _display_str(value, placeholder)
    return f"{number:,.2f}"


def _parse_float(value: object | None) -> float | None:
    """Parse a numeric display value when possible."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        text = str(value).replace(",", "").replace("%", "").strip()
        if not text or text == PLACEHOLDER:
            return None
        try:
            return float(text)
        except ValueError:
            return None


def _compose_total_equity(
    cash: object | None,
    unrealized: object | None,
    placeholder: str,
) -> str:
    """Compose total equity display from cash + unrealized when both numeric.

    Display-only aggregation of already-computed accounting fields.
    """
    cash_n = _parse_float(cash)
    unrealized_n = _parse_float(unrealized)
    if cash_n is None or unrealized_n is None:
        return placeholder
    return _format_money(cash_n + unrealized_n, placeholder)


def _bucket_order_status(status: str) -> str | None:
    """Map an order status token to a Paper Trading count bucket."""
    token = status.strip().lower().replace(" ", "_")
    if token in {"filled", "complete", "completed", "done"}:
        return "filled"
    if token in {"pending", "open", "submitted", "new", "accepted", "partial"}:
        return "pending"
    if token in {"cancelled", "canceled"}:
        return "cancelled"
    if token in {"rejected", "failed", "expired", "insufficient_capital"}:
        return "rejected"
    return None


def _count_order_buckets(
    orders: tuple[FacadeOrderRow, ...],
) -> tuple[str, str, str, str]:
    """Return display counts for filled/pending/cancelled/rejected."""
    filled = pending = cancelled = rejected = 0
    for order in orders:
        bucket = _bucket_order_status(order.status)
        if bucket == "filled":
            filled += 1
        elif bucket == "pending":
            pending += 1
        elif bucket == "cancelled":
            cancelled += 1
        elif bucket == "rejected":
            rejected += 1
    return str(filled), str(pending), str(cancelled), str(rejected)


def paper_ledger_to_page_view(
    ledger: FacadePaperTradingLedger,
) -> PaperTradingPageView:
    """Map facade paper ledger to presentation ``PaperTradingPageView``.

    Args:
        ledger: Facade paper trading ledger DTO.

    Returns:
        Presentation view for the Paper Trading page.
    """
    return PaperTradingPageView(
        virtual_cash=ledger.available_cash,
        available_cash=ledger.available_cash,
        capital_used=ledger.capital_used,
        total_equity=ledger.total_equity,
        todays_pnl=ledger.todays_pnl,
        realized_pnl=ledger.realized_pnl,
        unrealized_pnl=ledger.unrealized_pnl,
        orders_filled=ledger.orders_filled,
        orders_pending=ledger.orders_pending,
        orders_cancelled=ledger.orders_cancelled,
        orders_rejected=ledger.orders_rejected,
        positions=tuple(
            PaperPositionView(
                symbol=row.symbol,
                strategy=row.strategy,
                quantity=row.quantity,
                entry=row.entry,
                current=row.current,
                mtm=row.mtm,
                status=row.status,
                avg_price=row.entry,
                mark=row.current,
                pnl=row.mtm,
            )
            for row in ledger.positions
        ),
        orders=tuple(
            OrderRowView(
                order_id=row.order_id,
                plan_id=row.plan_id,
                status=row.status,
                symbol=row.symbol,
                side=row.side,
                quantity=row.quantity,
                timestamp=row.timestamp,
            )
            for row in ledger.orders
        ),
        equity_series=ledger.equity_series,
        source=ledger.source,
    )


def empty_order_book(*, as_of: datetime | None = None) -> FacadeOrderBook:
    """Return empty order book snapshot."""
    ts = as_of or _utc_now()
    return FacadeOrderBook(
        **_meta(as_of=ts, source="offline"),
        orders=(),
    )


def empty_portfolio(
    *,
    as_of: datetime | None = None,
    placeholder: str = "—",
) -> FacadePortfolio:
    """Return empty portfolio snapshot."""
    ts = as_of or _utc_now()
    return FacadePortfolio(
        **_meta(as_of=ts, source="offline"),
        equity=placeholder,
        exposure=placeholder,
        utilization=placeholder,
        positions=(),
        equity_series=(),
        allocation_series=(),
    )


def empty_risk(
    *,
    as_of: datetime | None = None,
    placeholder: str = "—",
) -> FacadeRisk:
    """Return empty risk snapshot."""
    ts = as_of or _utc_now()
    return FacadeRisk(
        **_meta(as_of=ts, source="offline"),
        verdict=placeholder,
        reason_codes=(),
        limits=(),
    )


def empty_apme(
    *,
    as_of: datetime | None = None,
    placeholder: str = "—",
) -> FacadeApme:
    """Return empty APME snapshot."""
    ts = as_of or _utc_now()
    return FacadeApme(
        **_meta(as_of=ts, source="offline"),
        summary=placeholder,
        decisions=(),
    )


def empty_logs(
    *,
    as_of: datetime | None = None,
    limit: int = 200,
) -> FacadeLogs:
    """Return empty logs snapshot."""
    ts = as_of or _utc_now()
    return FacadeLogs(
        **_meta(as_of=ts, source="offline"),
        entries=(),
        limit=limit,
    )


def empty_performance(*, as_of: datetime | None = None) -> FacadePerformance:
    """Return empty performance snapshot."""
    ts = as_of or _utc_now()
    return FacadePerformance(
        **_meta(as_of=ts, source="offline"),
        metrics=(),
        series=(),
    )


def to_jsonable(dto: object) -> Mapping[str, object]:
    """Convert a facade DTO to a JSON-serializable mapping.

    Args:
        dto: Frozen dataclass instance or mapping.

    Returns:
        JSON-friendly mapping with ISO datetimes and list tuples.
    """

    def _convert(value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, tuple):
            return [_convert(item) for item in value]
        if isinstance(value, Mapping):
            return {str(k): _convert(v) for k, v in value.items()}
        if hasattr(value, "__dataclass_fields__"):
            return _convert(asdict(value))  # type: ignore[arg-type]
        return value

    if isinstance(dto, Mapping):
        return {str(k): _convert(v) for k, v in dto.items()}
    if hasattr(dto, "__dataclass_fields__"):
        return _convert(asdict(dto))  # type: ignore[arg-type]
    raise DashboardFacadeValidationError(f"Unsupported DTO type: {type(dto)!r}")


def _display_str(value: object | None, placeholder: str) -> str:
    """Format upstream value for display or return placeholder."""
    if value is None:
        return placeholder
    text = str(value).strip()
    return text if text else placeholder


def _attr(obj: object, name: str, default: object = None) -> object:
    """Safely read an attribute from an upstream object."""
    return getattr(obj, name, default)


def _tuple_str(value: object) -> tuple[str, ...]:
    """Materialize an upstream sequence as ``tuple[str, ...]``."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(str(item) for item in value)
    except TypeError:
        return ()


def _tuple_pairs(value: object) -> tuple[tuple[str, float], ...]:
    """Materialize chart series pairs from upstream data."""
    if value is None:
        return ()
    result: list[tuple[str, float]] = []
    try:
        for item in value:  # type: ignore[union-attr]
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                result.append((str(item[0]), float(item[1])))
            elif isinstance(item, Mapping):
                label = item.get("label") or item.get("x") or item.get("name")
                val = item.get("value") or item.get("y")
                if label is not None and val is not None:
                    result.append((str(label), float(val)))
    except (TypeError, ValueError):
        return ()
    return tuple(result)


def _tuple_rows(value: object) -> tuple[tuple[str, ...], ...]:
    """Materialize tabular rows from upstream data."""
    if value is None:
        return ()
    rows: list[tuple[str, ...]] = []
    try:
        for item in value:  # type: ignore[union-attr]
            if isinstance(item, (list, tuple)):
                rows.append(tuple(str(cell) for cell in item))
            elif isinstance(item, Mapping):
                rows.append(tuple(str(v) for v in item.values()))
    except TypeError:
        return ()
    return tuple(rows)


def _redact_text(text: str, secret_keys: tuple[str, ...]) -> str:
    """Redact secret-like substrings from free-form text."""
    redacted = _SECRET_PATTERN.sub(r"\1\2***", text)
    lowered = redacted.lower()
    for fragment in secret_keys:
        if fragment in lowered:
            redacted = re.sub(
                rf"(?i)({re.escape(fragment)})(\s*[:=]\s*)(\S+)",
                r"\1\2***",
                redacted,
            )
    if len(redacted) > MAX_LOG_MESSAGE_LENGTH:
        return redacted[:MAX_LOG_MESSAGE_LENGTH] + "…"
    return redacted


def _redact_mapping(
    data: Mapping[str, object],
    secret_keys: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    """Redact secret values from mapping entries."""
    pairs: list[tuple[str, str]] = []
    for key, value in data.items():
        key_lower = str(key).lower()
        if any(fragment in key_lower for fragment in secret_keys):
            pairs.append((str(key), "***"))
        else:
            pairs.append((str(key), _redact_text(str(value), secret_keys)))
    return tuple(pairs)


class DashboardIntegrationFacade:
    """Read-only aggregation facade between backend session and dashboard UI."""

    def __init__(
        self,
        session: IntegrationSessionLike | None = None,
        *,
        config: DashboardIntegrationFacadeConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create a read-only dashboard integration facade.

        Args:
            session: Optional live Integration Session. ``None`` enables offline mode.
            config: Frozen facade configuration.
            clock: Injectable clock for cache timestamps and tests.
        """
        self._session = session
        self._config = config or DashboardIntegrationFacadeConfig()
        self._clock = clock or _utc_now
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[datetime, object]] = {}
        self._last_refresh_at: datetime | None = None
        self._last_error_code: str | None = None

    @property
    def schema_version(self) -> str:
        """Return facade DTO schema version."""
        return DASHBOARD_FACADE_SCHEMA_VERSION

    @property
    def is_connected(self) -> bool:
        """Return whether a live backend session is attached and reporting connected."""
        if self._session is None:
            return False
        with self._lock:
            try:
                status = self._build_system_status(source="live")
                return status.system_status not in {"DISCONNECTED", "UNKNOWN"}
            except Exception:
                return False

    def get_facade_health(self) -> FacadeHealthReport:
        """Return facade-local health report."""
        with self._lock:
            connected = self._session is not None and self.is_connected
            if self._session is None:
                status = "OFFLINE"
            elif self._last_error_code is not None:
                status = "DEGRADED"
            elif connected:
                status = "HEALTHY"
            else:
                status = "DEGRADED"
            return FacadeHealthReport(
                schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
                connected=connected,
                status=status,
                cache_entries=len(self._cache),
                last_refresh_at=self._last_refresh_at,
                last_error_code=self._last_error_code,
            )

    def get_system_status(self) -> FacadeSystemStatus:
        """Return aggregated system, broker, mode, and market status."""
        return self._cached_get("get_system_status", self._fetch_system_status)

    def get_market_snapshot(self) -> FacadeMarketSnapshot:
        """Return market display snapshot."""
        return self._cached_get("get_market_snapshot", self._fetch_market_snapshot)

    def get_home_market_indices(self) -> FacadeHomeMarketIndices:
        """Return the four Home terminal index quotes for display.

        Aggregates already-available upstream market/index fields via the
        injected session when present; otherwise returns offline placeholders.
        Does not fetch broker quotes or start trading cycles.
        """
        return self._cached_get(
            "get_home_market_indices",
            self._fetch_home_market_indices,
        )

    def get_strategy_status(self) -> FacadeStrategyStatus:
        """Return the Strategy Monitor evaluation snapshot for display.

        Aggregates already-available upstream evaluation fields via the
        injected session when present; otherwise returns offline placeholders.
        Does not evaluate strategies, select strategies, or start trading cycles.
        """
        return self._cached_get("get_strategy_status", self._fetch_strategy_status)

    def get_paper_positions(self) -> FacadePaperPositions:
        """Return paper capital and position display rows."""
        return self._cached_get("get_paper_positions", self._fetch_paper_positions)

    def get_paper_trading_ledger(self) -> FacadePaperTradingLedger:
        """Return Paper Trading page ledger snapshot for display.

        Aggregates already-available paper capital, positions, and order
        summaries via the injected session when present; otherwise returns
        offline placeholders. Does not place trades or run simulations.
        """
        return self._cached_get(
            "get_paper_trading_ledger",
            self._fetch_paper_trading_ledger,
        )

    def get_order_book(self) -> FacadeOrderBook:
        """Return recent order summary rows."""
        return self._cached_get("get_order_book", self._fetch_order_book)

    def get_portfolio(self) -> FacadePortfolio:
        """Return portfolio metrics and position rows."""
        return self._cached_get("get_portfolio", self._fetch_portfolio)

    def get_risk(self) -> FacadeRisk:
        """Return last risk verdict summary and redacted limits."""
        return self._cached_get("get_risk", self._fetch_risk)

    def get_apme(self) -> FacadeApme:
        """Return informational APME decision summaries."""
        return self._cached_get("get_apme", self._fetch_apme)

    def get_logs(self, *, limit: int | None = None) -> FacadeLogs:
        """Return bounded, redacted log entries (newest-first).

        Args:
            limit: Maximum entries to return; defaults to config ``log_limit_default``.
        """
        applied_limit = limit if limit is not None else self._config.log_limit_default
        cache_key = f"get_logs:{applied_limit}"
        return self._cached_get(cache_key, lambda: self._fetch_logs(applied_limit))

    def get_performance(self) -> FacadePerformance:
        """Return performance/analytics aggregates when available."""
        return self._cached_get("get_performance", self._fetch_performance)

    def refresh(self) -> FacadeRefreshResult:
        """Invalidate caches and re-read upstream snapshots without trading."""
        with self._lock:
            self._cache.clear()
            refreshed_at = self._clock()
            self._last_refresh_at = refreshed_at
            self._last_error_code = None
        try:
            self.get_system_status()
            message = "Snapshots refreshed"
            success = True
        except Exception as exc:
            message = f"Refresh completed with errors: {exc}"
            success = False
        return FacadeRefreshResult(
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
            refreshed_at=refreshed_at,
            cache_cleared=True,
            success=success,
            message=message,
        )

    def as_presentation_facade(self) -> DashboardBackendFacade:
        """Return a presentation Protocol adapter over this facade."""
        return PresentationFacadeAdapter(self)

    def start(self) -> FacadeActionResult:
        """Delegate session start when lifecycle passthrough is enabled."""
        if not self._config.enable_lifecycle_passthrough:
            return FacadeActionResult(
                success=False,
                message="Lifecycle passthrough disabled",
                code="DIF.LIFECYCLE.DISABLED",
            )
        if self._session is None:
            return FacadeActionResult(
                success=False,
                message="Backend session not connected",
                code="DIF.SESSION.UNAVAILABLE",
            )
        if not hasattr(self._session, "start"):
            return FacadeActionResult(
                success=False,
                message="Session does not support start",
                code="DIF.UPSTREAM.UNSUPPORTED",
            )
        try:
            self._session.start()  # type: ignore[attr-defined]
            return FacadeActionResult(success=True, message="Session started")
        except Exception as exc:
            with self._lock:
                self._last_error_code = "DIF.UPSTREAM.ERROR"
            return FacadeActionResult(
                success=False,
                message=str(exc),
                code="DIF.UPSTREAM.ERROR",
            )

    def stop(self) -> FacadeActionResult:
        """Delegate session stop when lifecycle passthrough is enabled."""
        if not self._config.enable_lifecycle_passthrough:
            return FacadeActionResult(
                success=False,
                message="Lifecycle passthrough disabled",
                code="DIF.LIFECYCLE.DISABLED",
            )
        if self._session is None:
            return FacadeActionResult(
                success=False,
                message="Backend session not connected",
                code="DIF.SESSION.UNAVAILABLE",
            )
        if not hasattr(self._session, "stop"):
            return FacadeActionResult(
                success=False,
                message="Session does not support stop",
                code="DIF.UPSTREAM.UNSUPPORTED",
            )
        try:
            self._session.stop()  # type: ignore[attr-defined]
            return FacadeActionResult(success=True, message="Session stopped")
        except Exception as exc:
            with self._lock:
                self._last_error_code = "DIF.UPSTREAM.ERROR"
            return FacadeActionResult(
                success=False,
                message=str(exc),
                code="DIF.UPSTREAM.ERROR",
            )

    def _cached_get(self, key: str, builder: Callable[[], object]) -> Any:
        """Return cached DTO or build under lock."""
        with self._lock:
            ttl = self._config.cache_ttl_seconds
            if ttl > 0 and key in self._cache:
                cached_at, cached_value = self._cache[key]
                age = (self._clock() - cached_at).total_seconds()
                if age <= ttl:
                    if hasattr(cached_value, "__dataclass_fields__"):
                        return replace(cached_value, source="cached")  # type: ignore[type-var]
                    return cached_value
                del self._cache[key]
        dto = builder()
        with self._lock:
            if self._config.cache_ttl_seconds > 0:
                self._cache[key] = (self._clock(), dto)
        return dto

    def _record_upstream_error(self, code: str) -> None:
        """Record last upstream error under lock."""
        with self._lock:
            self._last_error_code = code

    def _fetch_system_status(self) -> FacadeSystemStatus:
        """Build system status from session or offline defaults."""
        if self._session is None:
            return empty_system_status(
                as_of=self._clock(),
                placeholder=self._config.placeholder,
            )
        try:
            return self._build_system_status(source="live")
        except Exception:
            self._record_upstream_error("DIF.UPSTREAM.ERROR")
            return empty_system_status(
                as_of=self._clock(),
                facade_healthy=False,
            )

    def _build_system_status(self, *, source: str) -> FacadeSystemStatus:
        """Map upstream health/runtime into system status."""
        assert self._session is not None
        health = self._session.get_health()
        runtime = self._session.get_runtime_state()
        ph = self._config.placeholder

        session_state = _attr(health, "session_state")
        overall = _attr(health, "overall_status")
        system_status = "UNKNOWN"
        state_name = str(getattr(session_state, "value", session_state or "")).upper()
        overall_name = str(getattr(overall, "value", overall or "")).upper()

        if state_name in {"RUNNING"}:
            system_status = "RUNNING"
        elif state_name in {"DEGRADED"}:
            system_status = "DEGRADED"
        elif state_name in {"STOPPED", "STOPPING"}:
            system_status = "STOPPED"
        elif state_name in {"FAILED", "NOT_BOOTSTRAPPED"}:
            system_status = "DISCONNECTED"
        elif overall_name:
            system_status = overall_name if overall_name in {
                "RUNNING", "STOPPED", "DEGRADED", "DISCONNECTED", "UNKNOWN"
            } else "UNKNOWN"

        broker_snapshot = _attr(health, "broker_connection")
        broker_state = _attr(broker_snapshot, "state") if broker_snapshot else None
        broker_name = str(getattr(broker_state, "value", broker_state or "")).upper()
        if broker_name in {"CONNECTED"}:
            broker_status = "CONNECTED"
        elif broker_name in {"DISCONNECTED", "FAILED"}:
            broker_status = "DISCONNECTED"
        else:
            broker_connected = _attr(broker_snapshot, "connected")
            if broker_connected is True:
                broker_status = "CONNECTED"
            elif broker_connected is False:
                broker_status = "DISCONNECTED"
            else:
                broker_status = "N/A"

        execution_mode = _display_str(
            _attr(runtime, "execution_mode"),
            "ANALYSIS",
        ).upper()
        market_status = _display_str(
            _attr(runtime, "market_status", _attr(health, "market_status")),
            "UNKNOWN",
        ).upper()
        message = _display_str(_attr(health, "message"), "Integration session active")

        return FacadeSystemStatus(
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
            as_of=self._clock(),
            source=source,
            system_status=system_status,
            broker_status=broker_status,
            execution_mode=execution_mode,
            market_status=market_status,
            message=message,
            facade_healthy=True,
        )

    def _call_optional(self, method: str) -> object | None:
        """Call optional session accessor with soft degrade."""
        if self._session is None or not hasattr(self._session, method):
            self._record_upstream_error("DIF.UPSTREAM.UNSUPPORTED")
            return None
        try:
            return getattr(self._session, method)()
        except Exception:
            self._record_upstream_error("DIF.UPSTREAM.ERROR")
            return None

    def _try_optional(self, method: str) -> object | None:
        """Call optional session accessor without recording missing-method errors."""
        if self._session is None or not hasattr(self._session, method):
            return None
        try:
            return getattr(self._session, method)()
        except Exception:
            self._record_upstream_error("DIF.UPSTREAM.ERROR")
            return None

    def _try_optional_concrete(self, method: str) -> object | None:
        """Call optional accessor and ignore bare unittest.mock return values."""
        result = self._try_optional(method)
        if result is None:
            return None
        module = type(result).__module__
        if module == "unittest.mock":
            return None
        return result

    def _fetch_home_market_indices(self) -> FacadeHomeMarketIndices:
        """Build Home index quotes from optional upstream accessors."""
        as_of = self._clock()
        ph = self._config.placeholder
        system = self.get_system_status()
        connected = self.is_connected
        if self._session is None:
            return empty_home_market_indices(
                as_of=as_of,
                placeholder=ph,
                market_status=system.market_status,
                facade_connected=False,
                source="offline",
                connection_status="OFFLINE",
            )

        raw = (
            self._try_optional("get_home_market_indices")
            or self._try_optional("get_index_quotes")
        )
        by_symbol: dict[str, object] = {}
        if raw is not None:
            indices_raw = _attr(raw, "indices", raw)
            try:
                for item in indices_raw:  # type: ignore[union-attr]
                    if isinstance(item, Mapping):
                        symbol = _display_str(item.get("symbol"), "")
                    else:
                        symbol = _display_str(_attr(item, "symbol"), "")
                    if symbol:
                        by_symbol[symbol] = item
            except TypeError:
                by_symbol = {}

        if not by_symbol:
            market = self._try_optional("get_market_snapshot")
            index_map = _attr(market, "indices", _attr(market, "index_quotes", None))
            if isinstance(index_map, Mapping):
                by_symbol = {str(key): value for key, value in index_map.items()}

        quotes: list[HomeIndexQuote] = []
        for symbol in HOME_MARKET_INDEX_SYMBOLS:
            item = by_symbol.get(symbol)
            if item is None:
                quotes.append(
                    HomeIndexQuote(
                        symbol=symbol,
                        ltp=ph,
                        change_abs=ph,
                        change_pct=ph,
                        last_update=ph,
                        connection_status="UNKNOWN" if connected else "OFFLINE",
                    )
                )
                continue
            ltp = _format_ltp(
                _field(item, "ltp", "last_price", "value"),
                ph,
            )
            change_abs = _format_change_abs(
                _field(item, "change_abs", "change", "net_change"),
                ph,
            )
            change_pct = _format_change_pct(
                _field(item, "change_pct", "change_percent", "pchange"),
                ph,
            )
            last_update = _format_timestamp(
                _field(item, "last_update", "timestamp", "exchange_timestamp"),
                ph,
            )
            explicit_status = _field(item, "connection_status", "status")
            is_stale = _field(item, "is_stale")
            age_seconds = _field(item, "age_seconds")
            if explicit_status is not None:
                connection_status = _display_str(explicit_status, "UNKNOWN").upper()
            elif not connected:
                connection_status = "OFFLINE"
            elif is_stale is True or (
                isinstance(age_seconds, (int, float)) and float(age_seconds) > 5.0
            ):
                connection_status = "DELAYED"
            elif ltp == ph:
                connection_status = "UNKNOWN"
            else:
                connection_status = "LIVE"
            quotes.append(
                HomeIndexQuote(
                    symbol=symbol,
                    ltp=ltp,
                    change_abs=change_abs,
                    change_pct=change_pct,
                    last_update=last_update,
                    connection_status=connection_status,
                )
            )

        source = "live" if any(q.ltp != ph for q in quotes) else "offline"
        return FacadeHomeMarketIndices(
            indices=tuple(quotes),
            as_of=as_of,
            source=source if connected else "offline",
            market_status=system.market_status,
            facade_connected=connected,
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
        )

    def _fetch_market_snapshot(self) -> FacadeMarketSnapshot:
        """Build market snapshot from optional upstream accessor."""
        ph = self._config.placeholder
        if self._session is None:
            return empty_market_snapshot(as_of=self._clock(), placeholder=ph)
        snap = self._call_optional("get_market_snapshot")
        if snap is None:
            return empty_market_snapshot(as_of=self._clock(), placeholder=ph)
        return FacadeMarketSnapshot(
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
            as_of=self._clock(),
            source="live",
            underlyings=_tuple_str(_attr(snap, "underlyings")),
            selected_underlying=_display_str(_attr(snap, "selected_underlying"), ph),
            ltp=_display_str(_attr(snap, "ltp"), ph),
            change=_display_str(_attr(snap, "change"), ph),
            volume=_display_str(_attr(snap, "volume"), ph),
            option_chain_columns=_tuple_str(
                _attr(snap, "option_chain_columns")
            ) or ("strike", "type", "ltp", "oi", "iv"),
            option_chain_rows=_tuple_rows(_attr(snap, "option_chain_rows")),
        )

    def _fetch_strategy_status(self) -> FacadeStrategyStatus:
        """Build Strategy Monitor snapshot from optional upstream accessors."""
        ph = self._config.placeholder
        as_of = self._clock()
        if self._session is None:
            return empty_strategy_status(as_of=as_of, placeholder=ph, source="offline")

        snap = self._try_optional("get_strategy_status") or self._try_optional(
            "get_strategy_evaluation_summary"
        )
        if snap is None:
            return empty_strategy_status(as_of=as_of, placeholder=ph, source="offline")

        rows_raw = (
            _attr(snap, "strategies")
            or _attr(snap, "reports")
            or _attr(snap, "ranked_reports")
            or _attr(snap, "rows")
            or ()
        )
        by_family: dict[str, object] = {}
        try:
            for item in rows_raw if rows_raw is not None else ():
                family_id = _normalize_family_id(
                    _field(
                        item,
                        "family",
                        "strategy_family",
                        "strategy_id",
                        "display_name",
                    )
                )
                if not family_id:
                    continue
                # Prefer first match; keep if exact monitor family.
                if family_id not in by_family:
                    by_family[family_id] = item
                display = _normalize_family_id(_field(item, "display_name"))
                for known_id, known_name in STRATEGY_MONITOR_FAMILIES:
                    if family_id == known_id or display == known_id:
                        by_family[known_id] = item
                        break
                    if _normalize_family_id(known_name) == family_id:
                        by_family[known_id] = item
                        break
        except TypeError:
            by_family = {}

        strategies: list[FacadeStrategyRow] = []
        for family_id, display_name in STRATEGY_MONITOR_FAMILIES:
            item = by_family.get(family_id)
            if item is None:
                strategies.append(
                    FacadeStrategyRow(
                        strategy_id=family_id,
                        family=family_id,
                        display_name=display_name,
                        status=ph,
                        confidence=ph,
                        last_signal=ph,
                        timestamp=ph,
                        reasons=(),
                        score=ph,
                        eligibility=ph,
                        reason=ph,
                    )
                )
                continue

            reason, reasons = _primary_reason(item, ph)
            confidence_raw = _field(
                item,
                "confidence",
                "confidence_score",
            )
            if confidence_raw is None and hasattr(item, "confidence"):
                confidence_raw = getattr(item, "confidence")
            strategies.append(
                FacadeStrategyRow(
                    strategy_id=_display_str(
                        _field(item, "strategy_id"),
                        family_id,
                    ),
                    family=family_id,
                    display_name=_display_str(
                        _field(item, "display_name"),
                        display_name,
                    ),
                    status=_display_str(
                        _field(item, "status", "evaluation_status"),
                        ph,
                    ),
                    confidence=_format_confidence(confidence_raw, ph),
                    last_signal=_display_str(
                        _field(item, "last_signal", "signal"),
                        ph,
                    ),
                    timestamp=_format_timestamp(
                        _field(
                            item,
                            "timestamp",
                            "evaluated_at",
                            "evaluation_time",
                        ),
                        ph,
                    ),
                    reasons=reasons,
                    score=_format_score(
                        _field(
                            item,
                            "score",
                            "ranking_score",
                            "suitability_score",
                        ),
                        ph,
                    ),
                    eligibility=_format_eligibility(item, ph),
                    reason=reason,
                )
            )

        summary = _attr(snap, "summary")
        active = _display_str(
            _field(
                snap,
                "active_strategy",
                "selected_strategy",
                "top_strategy_id",
            )
            or (_attr(summary, "top_strategy_id") if summary is not None else None),
            ph,
        )
        # Prefer human display name when active matches a monitor family.
        for family_id, display_name in STRATEGY_MONITOR_FAMILIES:
            if _normalize_family_id(active) == family_id or active == family_id:
                active = display_name
                break

        confidence_score = _format_confidence(
            _field(snap, "confidence_score", "confidence", "top_confidence"),
            ph,
        )
        if confidence_score == ph:
            for row in strategies:
                if row.strategy_id == _normalize_family_id(active) or row.display_name == active:
                    if row.confidence != ph:
                        confidence_score = row.confidence
                        break
            if confidence_score == ph:
                top_rank = _attr(summary, "top_ranking_score") if summary else None
                confidence_score = _format_score(top_rank, ph)

        evaluation_time = _format_timestamp(
            _field(
                snap,
                "evaluation_time",
                "evaluated_at",
                "timestamp",
            ),
            ph,
        )
        market_regime = _display_str(
            _field(snap, "market_regime", "regime", "regime_label"),
            ph,
        )
        if market_regime == ph:
            # Soft companion read — display only; never starts evaluation.
            regime_snap = self._try_optional("get_market_regime") or self._try_optional(
                "get_regime_snapshot"
            )
            market_regime = _display_str(
                _field(regime_snap, "regime", "market_regime", "label", "name")
                if regime_snap is not None
                else None,
                ph,
            )

        has_live = any(
            row.score != ph or row.status != ph or row.eligibility != ph
            for row in strategies
        )
        return FacadeStrategyStatus(
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
            as_of=as_of,
            source="live" if has_live else "offline",
            strategies=tuple(strategies),
            market_regime=market_regime,
            active_strategy=active,
            confidence_score=confidence_score,
            evaluation_time=evaluation_time,
        )

    def _fetch_paper_positions(self) -> FacadePaperPositions:
        """Build paper positions from optional upstream accessor."""
        ph = self._config.placeholder
        if self._session is None:
            return empty_paper_positions(as_of=self._clock(), placeholder=ph)
        snap = self._call_optional("get_paper_positions") or self._call_optional(
            "get_paper_trading_snapshot"
        )
        if snap is None:
            return empty_paper_positions(as_of=self._clock(), placeholder=ph)
        rows_raw = _attr(snap, "positions") or ()
        rows: list[FacadePaperPositionRow] = []
        for item in rows_raw if rows_raw is not None else ():
            rows.append(
                FacadePaperPositionRow(
                    symbol=_display_str(_attr(item, "symbol"), ph),
                    quantity=_display_str(_attr(item, "quantity"), ph),
                    avg_price=_display_str(
                        _attr(item, "avg_price", _attr(item, "average_price")),
                        ph,
                    ),
                    mark=_display_str(_attr(item, "mark", _attr(item, "mark_price")), ph),
                    pnl=_display_str(_attr(item, "pnl"), ph),
                )
            )
        return FacadePaperPositions(
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
            as_of=self._clock(),
            source="live",
            virtual_cash=_display_str(_attr(snap, "virtual_cash"), ph),
            realized_pnl=_display_str(_attr(snap, "realized_pnl"), ph),
            unrealized_pnl=_display_str(_attr(snap, "unrealized_pnl"), ph),
            positions=tuple(rows),
            equity_series=_tuple_pairs(_attr(snap, "equity_series")),
        )

    def _map_ledger_position(
        self,
        item: object,
        *,
        placeholder: str,
    ) -> FacadePaperLedgerPosition:
        """Map one upstream position object to a ledger position row."""
        ph = placeholder
        qty = _display_str(_field(item, "quantity", "qty"), ph)
        entry = _format_money(
            _field(item, "entry", "avg_price", "average_price"),
            ph,
        )
        current = _format_money(
            _field(item, "current", "mark", "mark_price"),
            ph,
        )
        mtm = _format_money(
            _field(item, "mtm", "unrealized_pnl", "pnl"),
            ph,
        )
        status = _display_str(_field(item, "status", "position_status"), ph)
        if status == ph and qty != ph:
            status = "OPEN"
        symbol = _display_str(
            _field(item, "symbol", "instrument_key"),
            ph,
        )
        strategy = _display_str(
            _field(item, "strategy", "strategy_id"),
            ph,
        )
        return FacadePaperLedgerPosition(
            symbol=symbol,
            strategy=strategy,
            quantity=qty,
            entry=entry,
            current=current,
            mtm=mtm,
            status=status,
        )

    def _map_ledger_orders(
        self,
        orders_raw: object,
        *,
        placeholder: str,
    ) -> tuple[FacadeOrderRow, ...]:
        """Map upstream order rows to facade order DTOs."""
        ph = placeholder
        rows: list[FacadeOrderRow] = []
        try:
            for item in orders_raw if orders_raw is not None else ():
                rows.append(
                    FacadeOrderRow(
                        order_id=_display_str(
                            _field(item, "order_id", "id"),
                            ph,
                        ),
                        plan_id=_display_str(_field(item, "plan_id"), ph),
                        status=_display_str(_field(item, "status"), ph),
                        symbol=_display_str(_field(item, "symbol"), ph),
                        side=_display_str(_field(item, "side"), ph),
                        quantity=_display_str(
                            _field(item, "quantity", "qty"),
                            ph,
                        ),
                        timestamp=_display_str(
                            _field(item, "timestamp", "updated_at", "created_at"),
                            ph,
                        ),
                    )
                )
        except TypeError:
            return ()
        return tuple(rows)

    def _fetch_paper_trading_ledger(self) -> FacadePaperTradingLedger:
        """Build Paper Trading ledger from optional upstream accessors."""
        ph = self._config.placeholder
        as_of = self._clock()
        if self._session is None:
            return empty_paper_trading_ledger(
                as_of=as_of,
                placeholder=ph,
                source="offline",
            )

        snap = (
            self._try_optional_concrete("get_paper_trading_ledger")
            or self._try_optional_concrete("get_paper_trading_snapshot")
            or self._try_optional_concrete("get_paper_positions")
        )
        if snap is None:
            return empty_paper_trading_ledger(
                as_of=as_of,
                placeholder=ph,
                source="offline",
            )

        capital = _attr(snap, "capital")
        portfolio = _attr(snap, "portfolio_view") or _attr(snap, "portfolio")
        positions_container = (
            _attr(snap, "positions")
            or _attr(snap, "position_book")
            or ( _attr(portfolio, "positions") if portfolio is not None else None )
        )
        if positions_container is not None and not isinstance(
            positions_container, (list, tuple)
        ):
            positions_raw = _attr(positions_container, "positions", positions_container)
        else:
            positions_raw = positions_container or ()

        positions: list[FacadePaperLedgerPosition] = []
        try:
            for item in positions_raw if positions_raw is not None else ():
                positions.append(self._map_ledger_position(item, placeholder=ph))
        except TypeError:
            positions = []

        cash_raw = _field(
            snap,
            "available_cash",
            "cash",
            "virtual_cash",
        )
        if cash_raw is None and capital is not None:
            cash_raw = _field(capital, "cash", "available_cash")
        capital_used_raw = _field(
            snap,
            "capital_used",
            "reserved_margin_hint",
            "used_margin",
            "gross_notional",
        )
        if capital_used_raw is None and capital is not None:
            capital_used_raw = _field(
                capital,
                "reserved_margin_hint",
                "capital_used",
            )
        if capital_used_raw is None and portfolio is not None:
            capital_used_raw = _field(portfolio, "gross_notional", "capital_used")

        realized_raw = _field(
            snap,
            "realized_pnl",
            "total_realized_pnl",
            "cumulative_realized_pnl",
        )
        if realized_raw is None and capital is not None:
            realized_raw = _field(capital, "cumulative_realized_pnl", "realized_pnl")
        if realized_raw is None and portfolio is not None:
            realized_raw = _field(portfolio, "total_realized_pnl", "realized_pnl")

        unrealized_raw = _field(
            snap,
            "unrealized_pnl",
            "total_unrealized_pnl",
        )
        if unrealized_raw is None and portfolio is not None:
            unrealized_raw = _field(portfolio, "total_unrealized_pnl", "unrealized_pnl")

        equity_raw = _field(
            snap,
            "total_equity",
            "equity",
            "net_liquidation",
        )
        if equity_raw is None and portfolio is not None:
            equity_raw = _field(portfolio, "total_equity", "equity")
        equity_display = _format_money(equity_raw, ph)
        if equity_display == ph:
            equity_display = _compose_total_equity(cash_raw, unrealized_raw, ph)

        todays_raw = _field(snap, "todays_pnl", "today_pnl", "daily_pnl")

        orders_snap = (
            self._try_optional_concrete("get_order_book")
            or self._try_optional_concrete("get_orders_snapshot")
            or self._try_optional_concrete("get_paper_orders")
        )
        orders_raw = _attr(snap, "orders")
        if orders_raw is None and orders_snap is not None:
            orders_raw = _attr(orders_snap, "orders", orders_snap)
        orders = self._map_ledger_orders(orders_raw, placeholder=ph)
        filled, pending, cancelled, rejected = _count_order_buckets(orders)

        # Allow upstream pre-aggregated counts when present.
        explicit_filled = _field(snap, "orders_filled", "filled_count")
        if explicit_filled is not None:
            filled = _display_str(explicit_filled, filled)
        explicit_pending = _field(snap, "orders_pending", "pending_count")
        if explicit_pending is not None:
            pending = _display_str(explicit_pending, pending)
        explicit_cancelled = _field(snap, "orders_cancelled", "cancelled_count")
        if explicit_cancelled is not None:
            cancelled = _display_str(explicit_cancelled, cancelled)
        explicit_rejected = _field(snap, "orders_rejected", "rejected_count")
        if explicit_rejected is not None:
            rejected = _display_str(explicit_rejected, rejected)

        has_live = any(
            (
                cash_raw is not None,
                realized_raw is not None,
                unrealized_raw is not None,
                bool(positions),
                bool(orders),
            )
        )
        return FacadePaperTradingLedger(
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
            as_of=as_of,
            source="live" if has_live else "offline",
            available_cash=_format_money(cash_raw, ph),
            capital_used=_format_money(capital_used_raw, ph),
            total_equity=equity_display,
            todays_pnl=_format_money(todays_raw, ph),
            realized_pnl=_format_money(realized_raw, ph),
            unrealized_pnl=_format_money(unrealized_raw, ph),
            positions=tuple(positions),
            orders_filled=filled,
            orders_pending=pending,
            orders_cancelled=cancelled,
            orders_rejected=rejected,
            orders=orders,
            equity_series=_tuple_pairs(_attr(snap, "equity_series")),
        )

    def _fetch_order_book(self) -> FacadeOrderBook:
        """Build order book from optional upstream accessor."""
        ph = self._config.placeholder
        if self._session is None:
            return empty_order_book(as_of=self._clock())
        snap = self._call_optional("get_order_book") or self._call_optional(
            "get_orders_snapshot"
        )
        if snap is None:
            return empty_order_book(as_of=self._clock())
        orders_raw = _attr(snap, "orders") or ()
        rows: list[FacadeOrderRow] = []
        for item in orders_raw if orders_raw is not None else ():
            rows.append(
                FacadeOrderRow(
                    order_id=_display_str(_attr(item, "order_id"), ph),
                    plan_id=_display_str(_attr(item, "plan_id"), ph),
                    status=_display_str(_attr(item, "status"), ph),
                    symbol=_display_str(_attr(item, "symbol"), ph),
                    side=_display_str(_attr(item, "side"), ph),
                    quantity=_display_str(_attr(item, "quantity"), ph),
                    timestamp=_display_str(_attr(item, "timestamp"), ph),
                )
            )
        return FacadeOrderBook(
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
            as_of=self._clock(),
            source="live",
            orders=tuple(rows),
        )

    def _fetch_portfolio(self) -> FacadePortfolio:
        """Build portfolio snapshot from optional upstream accessor."""
        ph = self._config.placeholder
        if self._session is None:
            return empty_portfolio(as_of=self._clock(), placeholder=ph)
        snap = self._call_optional("get_portfolio") or self._call_optional(
            "get_portfolio_snapshot"
        )
        if snap is None:
            return empty_portfolio(as_of=self._clock(), placeholder=ph)
        rows_raw = _attr(snap, "positions") or ()
        rows: list[FacadePortfolioPositionRow] = []
        for item in rows_raw if rows_raw is not None else ():
            rows.append(
                FacadePortfolioPositionRow(
                    symbol=_display_str(_attr(item, "symbol"), ph),
                    quantity=_display_str(_attr(item, "quantity"), ph),
                    exposure=_display_str(_attr(item, "exposure"), ph),
                    pnl=_display_str(_attr(item, "pnl"), ph),
                )
            )
        allocation = _attr(snap, "allocation_series") or _attr(snap, "allocation")
        return FacadePortfolio(
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
            as_of=self._clock(),
            source="live",
            equity=_display_str(_attr(snap, "equity"), ph),
            exposure=_display_str(_attr(snap, "exposure"), ph),
            utilization=_display_str(_attr(snap, "utilization"), ph),
            positions=tuple(rows),
            equity_series=_tuple_pairs(_attr(snap, "equity_series")),
            allocation_series=_tuple_pairs(allocation),
        )

    def _fetch_risk(self) -> FacadeRisk:
        """Build risk snapshot from optional upstream accessor."""
        ph = self._config.placeholder
        if self._session is None:
            return empty_risk(as_of=self._clock(), placeholder=ph)
        snap = self._call_optional("get_risk") or self._call_optional(
            "get_risk_decision"
        )
        if snap is None:
            return empty_risk(as_of=self._clock(), placeholder=ph)
        limits_raw = _attr(snap, "limits")
        limits: tuple[tuple[str, str], ...]
        if isinstance(limits_raw, Mapping):
            limits = _redact_mapping(limits_raw, self._config.redact_secret_keys)
        elif limits_raw is None:
            limits = ()
        else:
            limits = tuple(
                (row[0], row[1])
                for row in _tuple_rows(limits_raw)
                if len(row) >= 2
            )
        return FacadeRisk(
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
            as_of=self._clock(),
            source="live",
            verdict=_display_str(_attr(snap, "verdict"), ph),
            reason_codes=_tuple_str(_attr(snap, "reason_codes")),
            limits=limits,
        )

    def _fetch_apme(self) -> FacadeApme:
        """Build APME snapshot from optional upstream accessor."""
        ph = self._config.placeholder
        if self._session is None:
            return empty_apme(as_of=self._clock(), placeholder=ph)
        snap = self._call_optional("get_apme") or self._call_optional(
            "get_apme_decisions"
        )
        if snap is None:
            return empty_apme(as_of=self._clock(), placeholder=ph)
        decisions_raw = _attr(snap, "decisions") or ()
        rows: list[FacadeApmeDecisionRow] = []
        for item in decisions_raw if decisions_raw is not None else ():
            rows.append(
                FacadeApmeDecisionRow(
                    position_id=_display_str(
                        _attr(item, "position_id", _attr(item, "symbol")),
                        ph,
                    ),
                    action=_display_str(_attr(item, "action"), ph),
                    rationale=_display_str(
                        _attr(item, "rationale", _attr(item, "reason")),
                        ph,
                    ),
                    timestamp=_display_str(_attr(item, "timestamp"), ph),
                )
            )
        return FacadeApme(
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
            as_of=self._clock(),
            source="live",
            summary=_display_str(_attr(snap, "summary"), ph),
            decisions=tuple(rows),
        )

    def _fetch_logs(self, limit: int) -> FacadeLogs:
        """Build redacted logs from optional upstream accessor."""
        if self._session is None:
            return empty_logs(as_of=self._clock(), limit=limit)
        snap = self._call_optional("get_logs")
        entries_raw: object = ()
        if snap is not None:
            entries_raw = _attr(snap, "entries") or _attr(snap, "lines") or ()
        else:
            buffer = self._call_optional("get_log_buffer")
            if isinstance(buffer, list):
                entries_raw = buffer

        entries: list[FacadeLogEntry] = []
        for item in entries_raw if entries_raw is not None else ():
            message = _display_str(_attr(item, "message"), self._config.placeholder)
            message = _redact_text(message, self._config.redact_secret_keys)
            entries.append(
                FacadeLogEntry(
                    timestamp=_display_str(_attr(item, "timestamp"), self._config.placeholder),
                    level=_display_str(_attr(item, "level"), "INFO"),
                    message=message,
                    logger=_display_str(_attr(item, "logger", _attr(item, "source")), ""),
                )
            )
        entries = entries[:limit]
        return FacadeLogs(
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
            as_of=self._clock(),
            source="live" if self._session is not None else "offline",
            entries=tuple(entries),
            limit=limit,
        )

    def _fetch_performance(self) -> FacadePerformance:
        """Build performance snapshot from optional upstream accessor."""
        if self._session is None:
            return empty_performance(as_of=self._clock())
        snap = self._call_optional("get_performance") or self._call_optional(
            "get_analytics"
        )
        if snap is None:
            return empty_performance(as_of=self._clock())
        metrics_raw = _attr(snap, "metrics")
        metrics: tuple[tuple[str, str], ...]
        if isinstance(metrics_raw, Mapping):
            metrics = tuple(
                (str(k), _display_str(v, self._config.placeholder))
                for k, v in metrics_raw.items()
            )
        else:
            metrics = _tuple_rows(metrics_raw)  # type: ignore[assignment]
        series = _tuple_pairs(_attr(snap, "series") or _attr(snap, "performance_series"))
        return FacadePerformance(
            schema_version=DASHBOARD_FACADE_SCHEMA_VERSION,
            as_of=self._clock(),
            source="live",
            metrics=metrics if isinstance(metrics, tuple) else (),
            series=series,
        )


class PresentationFacadeAdapter:
    """Thin adapter mapping integration facade DTOs to presentation view models."""

    def __init__(self, facade: DashboardIntegrationFacade) -> None:
        """Wrap a :class:`DashboardIntegrationFacade` for presentation Protocol use.

        Args:
            facade: Source integration facade instance.
        """
        self._facade = facade

    @property
    def is_connected(self) -> bool:
        """Return whether the underlying facade reports connected."""
        return self._facade.is_connected

    def get_health(self) -> SystemStatusView:
        """Map system status to presentation health view."""
        status = self._facade.get_system_status()
        return SystemStatusView(status=status.system_status, message=status.message)

    def get_runtime_state(self) -> RuntimeStateView:
        """Map system status fields to runtime state view."""
        status = self._facade.get_system_status()
        return RuntimeStateView(
            broker_status=status.broker_status,
            execution_mode=status.execution_mode,
            market_status=status.market_status,
            connected=self._facade.is_connected,
        )

    def get_home_snapshot(self) -> HomePageView:
        """Compose a home snapshot including live/placeholder index quotes."""
        paper = self._facade.get_paper_positions()
        strategy = self._facade.get_strategy_status()
        indices = home_indices_to_quote_views(self._facade.get_home_market_indices())
        active = (
            strategy.active_strategy
            if strategy.active_strategy != "—"
            else (strategy.strategies[0].display_name if strategy.strategies else "—")
        )
        confidence = (
            strategy.confidence_score
            if strategy.confidence_score != "—"
            else (strategy.strategies[0].confidence if strategy.strategies else "—")
        )
        return HomePageView(
            indices=indices,
            kpis=HomeKpiView(
                active_strategy=active,
                confidence=confidence,
                market_regime=strategy.market_regime,
                paper_pnl=paper.unrealized_pnl,
                open_positions=str(len(paper.positions)),
            ),
            cycle_summary=None,
        )

    def get_home_market_indices(self) -> FacadeHomeMarketIndices:
        """Expose home market indices through the presentation adapter."""
        return self._facade.get_home_market_indices()

    def get_market_snapshot(self) -> MarketPageView:
        """Map market facade DTO to presentation view."""
        snap = self._facade.get_market_snapshot()
        return MarketPageView(
            underlyings=snap.underlyings,
            selected_underlying=snap.selected_underlying,
            ltp=snap.ltp,
            change=snap.change,
            volume=snap.volume,
            option_chain_columns=snap.option_chain_columns,
            option_chain_rows=snap.option_chain_rows,
        )

    def get_strategy_monitor(self) -> StrategyMonitorView:
        """Map strategy status to presentation Strategy Monitor view."""
        return strategy_status_to_monitor_view(self._facade.get_strategy_status())

    def get_paper_trading(self) -> PaperTradingPageView:
        """Map paper trading ledger to presentation view."""
        return paper_ledger_to_page_view(self._facade.get_paper_trading_ledger())

    def get_paper_trading_ledger(self) -> FacadePaperTradingLedger:
        """Expose paper trading ledger through the presentation adapter."""
        return self._facade.get_paper_trading_ledger()

    def get_orders(self) -> OrdersPageView:
        """Map order book to presentation view."""
        snap = self._facade.get_order_book()
        return OrdersPageView(
            orders=tuple(
                OrderRowView(
                    order_id=row.order_id,
                    plan_id=row.plan_id,
                    status=row.status,
                    symbol=row.symbol,
                    side=row.side,
                    quantity=row.quantity,
                    timestamp=row.timestamp,
                )
                for row in snap.orders
            )
        )

    def get_portfolio(self) -> PortfolioPageView:
        """Map portfolio facade DTO to presentation view."""
        snap = self._facade.get_portfolio()
        return PortfolioPageView(
            equity=snap.equity,
            exposure=snap.exposure,
            utilization=snap.utilization,
            positions=tuple(
                PortfolioPositionView(
                    symbol=row.symbol,
                    quantity=row.quantity,
                    exposure=row.exposure,
                    pnl=row.pnl,
                )
                for row in snap.positions
            ),
            equity_series=snap.equity_series,
            allocation=snap.allocation_series,
        )

    def get_risk(self) -> RiskPageView:
        """Map risk facade DTO to presentation view."""
        snap = self._facade.get_risk()
        return RiskPageView(
            verdict=snap.verdict,
            reason_codes=snap.reason_codes,
            limits=MappingProxyType(dict(snap.limits)),
        )

    def get_apme(self) -> ApmePageView:
        """Map APME facade DTO to presentation view."""
        snap = self._facade.get_apme()
        return ApmePageView(
            decisions=tuple(
                ApmeDecisionView(
                    position_id=row.position_id,
                    action=row.action,
                    rationale=row.rationale,
                    timestamp=row.timestamp,
                )
                for row in snap.decisions
            ),
            hints=(),
        )

    def get_logs(self, *, limit: int = 200) -> LogsPageView:
        """Map logs facade DTO to presentation view."""
        snap = self._facade.get_logs(limit=limit)
        return LogsPageView(
            entries=tuple(
                LogEntryView(
                    level=entry.level,
                    message=entry.message,
                    timestamp=entry.timestamp,
                )
                for entry in snap.entries
            )
        )

    def get_analytics(self) -> AnalyticsPageView:
        """Map performance facade DTO to presentation view."""
        snap = self._facade.get_performance()
        metrics = dict(snap.metrics)
        return AnalyticsPageView(
            win_rate=metrics.get("win_rate", "—"),
            expectancy=metrics.get("expectancy", "—"),
            performance_series=snap.series,
            available=bool(snap.metrics or snap.series),
        )

    def get_settings_view(self) -> SettingsPageView:
        """Return redacted settings view derived from facade health."""
        health = self._facade.get_facade_health()
        return SettingsPageView(
            config_entries={
                "schema_version": self._facade.schema_version,
                "connected": str(health.connected),
                "status": health.status,
            },
            ui_preferences={"theme": "dark"},
        )

    def start(self) -> FacadeActionResult:
        """Delegate start to underlying facade."""
        return self._facade.start()

    def stop(self) -> FacadeActionResult:
        """Delegate stop to underlying facade."""
        return self._facade.stop()

    def refresh_snapshots(self) -> FacadeActionResult:
        """Refresh underlying facade caches."""
        result = self._facade.refresh()
        return FacadeActionResult(
            success=result.success,
            message=result.message,
        )


DashboardFacade = DashboardIntegrationFacade
