"""Canonical immutable market snapshot model for THETA AI TRADER.

This module defines the point-in-time market data contract consumed by
analytical engines. Snapshots are immutable after construction; adapters
assemble inputs and engines consume them read-only.
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Any, Final
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MARKET_SNAPSHOT_SCHEMA_VERSION: Final[str] = "1.0.0"

_ISO_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Stable error and warning codes (MARKET_SNAPSHOT.* namespace).
ERROR_PROVENANCE_NAIVE_TIMESTAMP: Final[str] = (
    "MARKET_SNAPSHOT.PROVENANCE.NAIVE_TIMESTAMP"
)
ERROR_PROVENANCE_NAIVE_CAPTURED_AT: Final[str] = (
    "MARKET_SNAPSHOT.PROVENANCE.NAIVE_CAPTURED_AT"
)
ERROR_PROVENANCE_MISSING_ID: Final[str] = "MARKET_SNAPSHOT.PROVENANCE.MISSING_ID"
ERROR_UNDERLYING_INVALID_SPOT: Final[str] = "MARKET_SNAPSHOT.UNDERLYING.INVALID_SPOT"
ERROR_CHAIN_INVALID_EXPIRY: Final[str] = "MARKET_SNAPSHOT.CHAIN.INVALID_EXPIRY"
ERROR_CHAIN_INVALID_STRIKE_STEP: Final[str] = "MARKET_SNAPSHOT.CHAIN.INVALID_STRIKE_STEP"
ERROR_CHAIN_ATM_OUT_OF_RANGE: Final[str] = "MARKET_SNAPSHOT.CHAIN.ATM_OUT_OF_RANGE"
ERROR_CHAIN_EMPTY: Final[str] = "MARKET_SNAPSHOT.CHAIN.EMPTY"
ERROR_CHAIN_DUPLICATE_CONTRACT: Final[str] = "MARKET_SNAPSHOT.CHAIN.DUPLICATE_CONTRACT"
ERROR_CHAIN_UNSORTED: Final[str] = "MARKET_SNAPSHOT.CHAIN.UNSORTED"
ERROR_CHAIN_CONTRACT_COUNT: Final[str] = "MARKET_SNAPSHOT.CHAIN.CONTRACT_COUNT_MISMATCH"
ERROR_CHAIN_COMPLETE_PAIRS: Final[str] = "MARKET_SNAPSHOT.CHAIN.INVALID_COMPLETE_PAIRS"
ERROR_CONTRACT_INVALID_STRIKE: Final[str] = "MARKET_SNAPSHOT.CONTRACT.INVALID_STRIKE"
ERROR_CONTRACT_INVALID_OPTION_TYPE: Final[str] = (
    "MARKET_SNAPSHOT.CONTRACT.INVALID_OPTION_TYPE"
)
ERROR_CONTRACT_INVALID_LTP: Final[str] = "MARKET_SNAPSHOT.CONTRACT.INVALID_LTP"
ERROR_CONTRACT_INVALID_BID: Final[str] = "MARKET_SNAPSHOT.CONTRACT.INVALID_BID"
ERROR_CONTRACT_INVALID_ASK: Final[str] = "MARKET_SNAPSHOT.CONTRACT.INVALID_ASK"
ERROR_CONTRACT_INVALID_VOLUME: Final[str] = "MARKET_SNAPSHOT.CONTRACT.INVALID_VOLUME"
ERROR_CONTRACT_INVALID_OI: Final[str] = "MARKET_SNAPSHOT.CONTRACT.INVALID_OI"
ERROR_CONTRACT_EXPIRY_MISMATCH: Final[str] = "MARKET_SNAPSHOT.CONTRACT.EXPIRY_MISMATCH"
ERROR_CONTRACT_UNDERLYING_MISMATCH: Final[str] = (
    "MARKET_SNAPSHOT.CONTRACT.UNDERLYING_MISMATCH"
)
ERROR_CONTRACT_STRIKE_OUT_OF_RANGE: Final[str] = (
    "MARKET_SNAPSHOT.CONTRACT.STRIKE_OUT_OF_RANGE"
)
ERROR_VOLATILITY_INVALID_PRICE: Final[str] = "MARKET_SNAPSHOT.VOLATILITY.INVALID_PRICE"
WARNING_CONTRACT_MISSING_BID: Final[str] = "MARKET_SNAPSHOT.CONTRACT.MISSING_BID"
WARNING_CONTRACT_MISSING_ASK: Final[str] = "MARKET_SNAPSHOT.CONTRACT.MISSING_ASK"
WARNING_CONTRACT_INVERTED_MARKET: Final[str] = "MARKET_SNAPSHOT.CONTRACT.INVERTED_MARKET"
WARNING_CHAIN_ATM_DRIFT: Final[str] = "MARKET_SNAPSHOT.CHAIN.ATM_DRIFT"
WARNING_FRESHNESS_OBSERVATION_AFTER_CAPTURE: Final[str] = (
    "MARKET_SNAPSHOT.FRESHNESS.OBSERVATION_AFTER_CAPTURE"
)
ERROR_SCHEMA_UNSUPPORTED: Final[str] = "MARKET_SNAPSHOT.SCHEMA.UNSUPPORTED_VERSION"
ERROR_DESERIALIZATION: Final[str] = "MARKET_SNAPSHOT.DESERIALIZATION.INVALID"


class SnapshotFreshnessStatus(str, Enum):
    """Machine-readable freshness classification."""

    FRESH = "FRESH"
    STALE = "STALE"
    MARKET_CLOSED = "MARKET_CLOSED"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    UNKNOWN = "UNKNOWN"


class SnapshotSource(str, Enum):
    """Origin of a snapshot."""

    LIVE = "LIVE"
    REPLAY = "REPLAY"
    FIXTURE = "FIXTURE"
    BACKTEST = "BACKTEST"


class OptionType(str, Enum):
    """Normalized option side."""

    CE = "CE"
    PE = "PE"


class LiquidityBand(str, Enum):
    """Optional pre-computed liquidity band (not computed in v1)."""

    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    AVERAGE = "AVERAGE"
    POOR = "POOR"
    UNKNOWN = "UNKNOWN"


class SnapshotValidationStatus(str, Enum):
    """Overall snapshot validation outcome."""

    VALID = "VALID"
    PARTIAL = "PARTIAL"
    INVALID = "INVALID"


class SnapshotBuildError(Exception):
    """Raised when snapshot construction fails hard validation."""


class SnapshotValidationError(Exception):
    """Raised when deserialized snapshot data fails validation."""

    def __init__(
        self,
        message: str,
        *,
        code: str = ERROR_DESERIALIZATION,
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SnapshotErrorRecord:
    """Structured validation error attached to quality or result records."""

    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True)
class SnapshotWarningRecord:
    """Non-fatal validation warning."""

    code: str
    message: str
    field: str | None = None


@dataclass(frozen=True)
class SnapshotProvenance:
    """Identity and capture metadata for a snapshot."""

    snapshot_id: str
    schema_version: str
    source: SnapshotSource
    adapter_name: str
    as_of: datetime
    captured_at: datetime
    underlying_symbol: str
    exchange: str
    adapter_version: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class UnderlyingSnapshot:
    """Index or underlying spot observation."""

    symbol: str
    exchange: str
    quote_key: str
    last_price: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    quote_timestamp: datetime | None = None
    volume: int | None = None


@dataclass(frozen=True)
class VolatilitySnapshot:
    """Index volatility observation (e.g., India VIX)."""

    symbol: str
    exchange: str
    quote_key: str
    last_price: float
    quote_timestamp: datetime | None = None


@dataclass(frozen=True)
class OptionChainMetadata:
    """Option chain grid metadata."""

    underlying: str
    exchange: str
    expiry: str
    atm_strike: float
    strike_step: float
    strike_window_strikes: int
    minimum_strike: float
    maximum_strike: float
    lot_size: int
    contract_count: int
    complete_pairs: int


@dataclass(frozen=True)
class OptionContractSnapshot:
    """One normalized option contract observation."""

    underlying: str
    exchange: str
    tradingsymbol: str
    expiry: str
    strike: float
    option_type: OptionType
    lot_size: int
    ltp: float
    bid: float
    ask: float
    volume: int
    open_interest: int
    delta: float | None = None
    iv: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    instrument_token: int | None = None
    exchange_token: int | None = None
    tick_size: float | None = None
    quote_timestamp: datetime | None = None
    last_quantity: int | None = None
    average_price: float | None = None
    buy_quantity: int | None = None
    sell_quantity: int | None = None
    oi_day_high: int | None = None
    oi_day_low: int | None = None


@dataclass(frozen=True)
class OptionChainSnapshot:
    """Option chain slice with metadata and contracts."""

    metadata: OptionChainMetadata
    contracts: tuple[OptionContractSnapshot, ...]


@dataclass(frozen=True)
class SnapshotFreshness:
    """Freshness evaluation result."""

    status: SnapshotFreshnessStatus
    reference_time: datetime
    observation_time: datetime
    age_seconds: float
    market_session_open: bool
    max_age_seconds: float
    is_usable_for_live_decisions: bool
    reason: str


@dataclass(frozen=True)
class SnapshotQuality:
    """Validation quality summary stored on the snapshot."""

    validation_status: SnapshotValidationStatus
    completeness_score: float
    missing_quotes: int
    inverted_markets: int
    warnings: tuple[SnapshotWarningRecord, ...]
    errors: tuple[SnapshotErrorRecord, ...]
    expected_contract_count: int | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    """Canonical immutable point-in-time market observation."""

    provenance: SnapshotProvenance
    freshness: SnapshotFreshness
    quality: SnapshotQuality
    underlying: UnderlyingSnapshot
    option_chain: OptionChainSnapshot
    volatility: VolatilitySnapshot | None = None


@dataclass(frozen=True)
class SnapshotValidationResult:
    """Outcome of ``validate_market_snapshot``."""

    validation_status: SnapshotValidationStatus
    completeness_score: float
    missing_quotes: int
    inverted_markets: int
    warnings: tuple[SnapshotWarningRecord, ...]
    errors: tuple[SnapshotErrorRecord, ...]


@dataclass(frozen=True)
class ValidationPolicy:
    """Configurable validation strictness."""

    minimum_completeness_for_valid: float = 90.0
    minimum_completeness_for_partial: float = 70.0
    treat_warnings_as_errors: bool = False


@dataclass(frozen=True)
class SnapshotFreshnessPolicy:
    """Configurable freshness thresholds."""

    market_open: time = time(9, 15)
    market_close: time = time(15, 30)
    timezone: str = "Asia/Kolkata"
    max_quote_age_seconds_live: float = 120.0
    max_quote_age_seconds_pre_open: float = 900.0
    allow_market_closed_analysis: bool = True
    future_timestamp_tolerance_seconds: float = 2.0


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def _is_valid_quote_price(value: float) -> bool:
    return math.isfinite(value) and value > 0.0


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _contract_sort_key(contract: OptionContractSnapshot) -> tuple[float, str]:
    return (contract.strike, contract.option_type.value)


def _sort_contracts(
    contracts: Sequence[OptionContractSnapshot],
) -> tuple[OptionContractSnapshot, ...]:
    return tuple(sorted(contracts, key=_contract_sort_key))


def _count_complete_pairs(contracts: Sequence[OptionContractSnapshot]) -> int:
    strikes: dict[float, set[OptionType]] = {}
    for contract in contracts:
        strikes.setdefault(contract.strike, set()).add(contract.option_type)
    return sum(1 for sides in strikes.values() if OptionType.CE in sides and OptionType.PE in sides)


def _contracts_are_sorted(contracts: Sequence[OptionContractSnapshot]) -> bool:
    ordered = list(contracts)
    return ordered == sorted(ordered, key=_contract_sort_key)


def _compute_completeness_score(
    *,
    contract_count: int,
    missing_quotes: int,
    inverted_markets: int,
    strike_window_strikes: int,
    complete_pairs: int,
) -> float:
    expected_pairs = strike_window_strikes * 2
    score = 100.0
    score -= (missing_quotes / max(contract_count, 1)) * 40.0
    score -= (inverted_markets / max(contract_count, 1)) * 10.0
    score -= max(0, expected_pairs - complete_pairs) * 2.0
    return _clamp(score, 0.0, 100.0)


def _classify_validation_status(
    *,
    errors: Sequence[SnapshotErrorRecord],
    warnings: Sequence[SnapshotWarningRecord],
    completeness_score: float,
    policy: ValidationPolicy,
) -> SnapshotValidationStatus:
    if errors or completeness_score < policy.minimum_completeness_for_partial:
        return SnapshotValidationStatus.INVALID
    if (
        completeness_score < policy.minimum_completeness_for_valid
        or warnings
    ):
        return SnapshotValidationStatus.PARTIAL
    return SnapshotValidationStatus.VALID


def _validate_contract_fields(
    contract: OptionContractSnapshot,
    *,
    index: int,
    chain_metadata: OptionChainMetadata,
) -> tuple[list[SnapshotErrorRecord], list[SnapshotWarningRecord], bool, bool]:
    errors: list[SnapshotErrorRecord] = []
    warnings: list[SnapshotWarningRecord] = []
    missing_quote = False
    inverted_market = False
    prefix = f"option_chain.contracts[{index}]"

    if not math.isfinite(contract.strike) or contract.strike <= 0:
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CONTRACT_INVALID_STRIKE,
                message="Contract strike must be finite and greater than zero.",
                field=f"{prefix}.strike",
            )
        )

    if contract.option_type not in (OptionType.CE, OptionType.PE):
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CONTRACT_INVALID_OPTION_TYPE,
                message="Contract option_type must be CE or PE.",
                field=f"{prefix}.option_type",
            )
        )

    if not math.isfinite(contract.ltp) or contract.ltp < 0:
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CONTRACT_INVALID_LTP,
                message="Contract ltp must be finite and non-negative.",
                field=f"{prefix}.ltp",
            )
        )

    if not math.isfinite(contract.bid) or contract.bid < 0:
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CONTRACT_INVALID_BID,
                message="Contract bid must be finite and non-negative.",
                field=f"{prefix}.bid",
            )
        )
    elif not _is_valid_quote_price(contract.bid):
        missing_quote = True
        warnings.append(
            SnapshotWarningRecord(
                code=WARNING_CONTRACT_MISSING_BID,
                message="Contract bid is missing or zero.",
                field=f"{prefix}.bid",
            )
        )

    if not math.isfinite(contract.ask) or contract.ask < 0:
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CONTRACT_INVALID_ASK,
                message="Contract ask must be finite and non-negative.",
                field=f"{prefix}.ask",
            )
        )
    elif not _is_valid_quote_price(contract.ask):
        missing_quote = True
        warnings.append(
            SnapshotWarningRecord(
                code=WARNING_CONTRACT_MISSING_ASK,
                message="Contract ask is missing or zero.",
                field=f"{prefix}.ask",
            )
        )

    if (
        _is_valid_quote_price(contract.bid)
        and _is_valid_quote_price(contract.ask)
        and contract.ask < contract.bid
    ):
        inverted_market = True
        warnings.append(
            SnapshotWarningRecord(
                code=WARNING_CONTRACT_INVERTED_MARKET,
                message="Contract ask is below bid.",
                field=f"{prefix}",
            )
        )

    if contract.volume < 0:
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CONTRACT_INVALID_VOLUME,
                message="Contract volume must be non-negative.",
                field=f"{prefix}.volume",
            )
        )

    if contract.open_interest < 0:
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CONTRACT_INVALID_OI,
                message="Contract open_interest must be non-negative.",
                field=f"{prefix}.open_interest",
            )
        )

    if contract.expiry != chain_metadata.expiry:
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CONTRACT_EXPIRY_MISMATCH,
                message="Contract expiry must match chain metadata expiry.",
                field=f"{prefix}.expiry",
            )
        )

    if contract.underlying != chain_metadata.underlying:
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CONTRACT_UNDERLYING_MISMATCH,
                message="Contract underlying must match chain metadata underlying.",
                field=f"{prefix}.underlying",
            )
        )

    if not (
        chain_metadata.minimum_strike
        <= contract.strike
        <= chain_metadata.maximum_strike
    ):
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CONTRACT_STRIKE_OUT_OF_RANGE,
                message="Contract strike is outside the chain window.",
                field=f"{prefix}.strike",
            )
        )

    return errors, warnings, missing_quote, inverted_market


def validate_market_snapshot(
    snapshot: MarketSnapshot,
    *,
    policy: ValidationPolicy | None = None,
) -> SnapshotValidationResult:
    """Validate a snapshot across all specification layers.

    Args:
        snapshot: Snapshot to validate.
        policy: Optional validation policy overrides.

    Returns:
        Structured validation result without mutating the snapshot.
    """
    validation_policy = policy or ValidationPolicy()
    errors: list[SnapshotErrorRecord] = []
    warnings: list[SnapshotWarningRecord] = []
    missing_quotes = 0
    inverted_markets = 0

    provenance = snapshot.provenance
    underlying = snapshot.underlying
    chain = snapshot.option_chain
    metadata = chain.metadata
    contracts = chain.contracts

    if not _is_timezone_aware(provenance.as_of):
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_PROVENANCE_NAIVE_TIMESTAMP,
                message="provenance.as_of must be timezone-aware.",
                field="provenance.as_of",
            )
        )

    if not _is_timezone_aware(provenance.captured_at):
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_PROVENANCE_NAIVE_CAPTURED_AT,
                message="provenance.captured_at must be timezone-aware.",
                field="provenance.captured_at",
            )
        )

    if not provenance.snapshot_id.strip():
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_PROVENANCE_MISSING_ID,
                message="provenance.snapshot_id must be non-empty.",
                field="provenance.snapshot_id",
            )
        )

    if not math.isfinite(underlying.last_price) or underlying.last_price <= 0:
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_UNDERLYING_INVALID_SPOT,
                message="underlying.last_price must be finite and greater than zero.",
                field="underlying.last_price",
            )
        )

    if not _ISO_DATE_PATTERN.match(metadata.expiry):
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CHAIN_INVALID_EXPIRY,
                message="option_chain.metadata.expiry must match YYYY-MM-DD.",
                field="option_chain.metadata.expiry",
            )
        )

    if metadata.strike_step <= 0:
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CHAIN_INVALID_STRIKE_STEP,
                message="option_chain.metadata.strike_step must be greater than zero.",
                field="option_chain.metadata.strike_step",
            )
        )

    if not (
        metadata.minimum_strike
        <= metadata.atm_strike
        <= metadata.maximum_strike
    ):
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CHAIN_ATM_OUT_OF_RANGE,
                message="option_chain.metadata.atm_strike must be within strike window.",
                field="option_chain.metadata.atm_strike",
            )
        )

    if not contracts:
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CHAIN_EMPTY,
                message="option_chain.contracts must be non-empty.",
                field="option_chain.contracts",
            )
        )

    seen: set[tuple[float, OptionType]] = set()
    for contract in contracts:
        key = (contract.strike, contract.option_type)
        if key in seen:
            errors.append(
                SnapshotErrorRecord(
                    code=ERROR_CHAIN_DUPLICATE_CONTRACT,
                    message="Duplicate contract strike and option_type pair.",
                    field="option_chain.contracts",
                )
            )
            break
        seen.add(key)

    if contracts and not _contracts_are_sorted(contracts):
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CHAIN_UNSORTED,
                message="option_chain.contracts must be sorted by strike then option_type.",
                field="option_chain.contracts",
            )
        )

    for index, contract in enumerate(contracts):
        contract_errors, contract_warnings, missing, inverted = _validate_contract_fields(
            contract,
            index=index,
            chain_metadata=metadata,
        )
        errors.extend(contract_errors)
        warnings.extend(contract_warnings)
        if missing:
            missing_quotes += 1
        if inverted:
            inverted_markets += 1

    if abs(metadata.atm_strike - underlying.last_price) > metadata.strike_step:
        warnings.append(
            SnapshotWarningRecord(
                code=WARNING_CHAIN_ATM_DRIFT,
                message="ATM strike drift exceeds one strike step from spot.",
                field="option_chain.metadata.atm_strike",
            )
        )

    if metadata.contract_count != len(contracts):
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CHAIN_CONTRACT_COUNT,
                message="option_chain.metadata.contract_count must equal len(contracts).",
                field="option_chain.metadata.contract_count",
            )
        )

    unique_strikes = len({contract.strike for contract in contracts})
    if metadata.complete_pairs > unique_strikes:
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_CHAIN_COMPLETE_PAIRS,
                message="option_chain.metadata.complete_pairs exceeds unique strikes.",
                field="option_chain.metadata.complete_pairs",
            )
        )

    if snapshot.volatility is not None and (
        not math.isfinite(snapshot.volatility.last_price)
        or snapshot.volatility.last_price <= 0
    ):
        errors.append(
            SnapshotErrorRecord(
                code=ERROR_VOLATILITY_INVALID_PRICE,
                message="volatility.last_price must be finite and greater than zero.",
                field="volatility.last_price",
            )
        )

    observation_time = _select_observation_time(snapshot)
    if observation_time > provenance.captured_at + timedelta(seconds=5):
        warnings.append(
            SnapshotWarningRecord(
                code=WARNING_FRESHNESS_OBSERVATION_AFTER_CAPTURE,
                message="Observation time is after captured_at beyond tolerance.",
                field="freshness.observation_time",
            )
        )

    if validation_policy.treat_warnings_as_errors:
        errors.extend(
            SnapshotErrorRecord(
                code=warning.code,
                message=warning.message,
                field=warning.field,
            )
            for warning in warnings
        )
        warnings = []

    contract_count = len(contracts)
    completeness_score = _compute_completeness_score(
        contract_count=contract_count,
        missing_quotes=missing_quotes,
        inverted_markets=inverted_markets,
        strike_window_strikes=metadata.strike_window_strikes,
        complete_pairs=metadata.complete_pairs,
    )

    validation_status = _classify_validation_status(
        errors=errors,
        warnings=warnings,
        completeness_score=completeness_score,
        policy=validation_policy,
    )

    return SnapshotValidationResult(
        validation_status=validation_status,
        completeness_score=completeness_score,
        missing_quotes=missing_quotes,
        inverted_markets=inverted_markets,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def _select_observation_time(snapshot: MarketSnapshot) -> datetime:
    """Return effective observation timestamp for freshness evaluation."""
    candidates: list[datetime] = [snapshot.provenance.as_of]
    if snapshot.underlying.quote_timestamp is not None:
        candidates.append(snapshot.underlying.quote_timestamp)
    for contract in snapshot.option_chain.contracts:
        if contract.quote_timestamp is not None:
            candidates.append(contract.quote_timestamp)
    return min(candidates)


def _has_quote_timestamps(snapshot: MarketSnapshot) -> bool:
    if snapshot.underlying.quote_timestamp is not None:
        return True
    return any(
        contract.quote_timestamp is not None
        for contract in snapshot.option_chain.contracts
    )


def _is_market_session_open(
    reference_time: datetime,
    policy: SnapshotFreshnessPolicy,
) -> bool:
    tz = ZoneInfo(policy.timezone)
    localized = reference_time.astimezone(tz)
    if localized.weekday() >= 5:
        return False
    current_clock = localized.time()
    return policy.market_open <= current_clock <= policy.market_close


def _effective_max_age_seconds(
    reference_time: datetime,
    policy: SnapshotFreshnessPolicy,
) -> float:
    tz = ZoneInfo(policy.timezone)
    localized = reference_time.astimezone(tz)
    if localized.weekday() >= 5:
        return policy.max_quote_age_seconds_live
    current_clock = localized.time()
    if current_clock < policy.market_open:
        return policy.max_quote_age_seconds_pre_open
    return policy.max_quote_age_seconds_live


def evaluate_snapshot_freshness(
    snapshot: MarketSnapshot,
    *,
    reference_time: datetime,
    policy: SnapshotFreshnessPolicy | None = None,
) -> SnapshotFreshness:
    """Evaluate snapshot freshness against a reference clock.

    Args:
        snapshot: Snapshot to evaluate.
        reference_time: Time used for age calculation (must be timezone-aware).
        policy: Optional freshness policy overrides.

    Returns:
        Freshness record describing session state and usability.
    """
    freshness_policy = policy or SnapshotFreshnessPolicy()
    if not _is_timezone_aware(reference_time):
        raise ValueError("reference_time must be timezone-aware.")

    if not _has_quote_timestamps(snapshot):
        observation_time = snapshot.provenance.as_of
        return SnapshotFreshness(
            status=SnapshotFreshnessStatus.UNKNOWN,
            reference_time=reference_time,
            observation_time=observation_time,
            age_seconds=max(
                0.0,
                (reference_time - observation_time).total_seconds(),
            ),
            market_session_open=_is_market_session_open(
                reference_time,
                freshness_policy,
            ),
            max_age_seconds=_effective_max_age_seconds(
                reference_time,
                freshness_policy,
            ),
            is_usable_for_live_decisions=False,
            reason="No broker quote timestamps available on snapshot.",
        )

    observation_time = _select_observation_time(snapshot)
    age_seconds = (reference_time - observation_time).total_seconds()
    market_open = _is_market_session_open(reference_time, freshness_policy)
    max_age_seconds = _effective_max_age_seconds(reference_time, freshness_policy)
    tolerance = freshness_policy.future_timestamp_tolerance_seconds

    if age_seconds < -tolerance:
        return SnapshotFreshness(
            status=SnapshotFreshnessStatus.FUTURE_TIMESTAMP,
            reference_time=reference_time,
            observation_time=observation_time,
            age_seconds=age_seconds,
            market_session_open=market_open,
            max_age_seconds=max_age_seconds,
            is_usable_for_live_decisions=False,
            reason="Observation timestamp is ahead of reference time.",
        )

    if not market_open:
        return SnapshotFreshness(
            status=SnapshotFreshnessStatus.MARKET_CLOSED,
            reference_time=reference_time,
            observation_time=observation_time,
            age_seconds=max(0.0, age_seconds),
            market_session_open=False,
            max_age_seconds=max_age_seconds,
            is_usable_for_live_decisions=False,
            reason="Market session is closed.",
        )

    if age_seconds > max_age_seconds:
        return SnapshotFreshness(
            status=SnapshotFreshnessStatus.STALE,
            reference_time=reference_time,
            observation_time=observation_time,
            age_seconds=age_seconds,
            market_session_open=True,
            max_age_seconds=max_age_seconds,
            is_usable_for_live_decisions=False,
            reason="Quote age exceeds live threshold during open session.",
        )

    return SnapshotFreshness(
        status=SnapshotFreshnessStatus.FRESH,
        reference_time=reference_time,
        observation_time=observation_time,
        age_seconds=age_seconds,
        market_session_open=True,
        max_age_seconds=max_age_seconds,
        is_usable_for_live_decisions=True,
        reason="Quote age within live threshold during open session.",
    )


def is_live_trade_ready(
    snapshot: MarketSnapshot,
    *,
    strict: bool = True,
) -> bool:
    """Return whether a snapshot is usable for live trade-enabling paths.

    Args:
        snapshot: Snapshot to inspect.
        strict: When True, require VALID quality; when False, allow PARTIAL.

    Returns:
        True only when freshness and quality thresholds are satisfied.
    """
    allowed_statuses = {SnapshotValidationStatus.VALID}
    if not strict:
        allowed_statuses.add(SnapshotValidationStatus.PARTIAL)

    return (
        snapshot.quality.validation_status in allowed_statuses
        and snapshot.freshness.is_usable_for_live_decisions
        and snapshot.freshness.status == SnapshotFreshnessStatus.FRESH
    )


def with_freshness(
    snapshot: MarketSnapshot,
    freshness: SnapshotFreshness,
) -> MarketSnapshot:
    """Return a copy of the snapshot with an updated freshness block."""
    return replace(snapshot, freshness=freshness)


def build_market_snapshot(
    *,
    underlying: UnderlyingSnapshot,
    contracts: Sequence[OptionContractSnapshot],
    underlying_symbol: str,
    exchange: str,
    expiry: str,
    atm_strike: float,
    strike_step: float,
    strike_window_strikes: int,
    minimum_strike: float,
    maximum_strike: float,
    lot_size: int,
    as_of: datetime,
    captured_at: datetime | None = None,
    source: SnapshotSource = SnapshotSource.LIVE,
    adapter_name: str = "market_data_adapter",
    adapter_version: str | None = None,
    correlation_id: str | None = None,
    snapshot_id: str | None = None,
    volatility: VolatilitySnapshot | None = None,
    freshness_policy: SnapshotFreshnessPolicy | None = None,
    validation_policy: ValidationPolicy | None = None,
    reference_time: datetime | None = None,
    strict: bool = False,
) -> MarketSnapshot:
    """Assemble, validate, and return a canonical market snapshot.

    Args:
        underlying: Spot/index observation.
        contracts: Option contracts for the chain slice.
        underlying_symbol: Canonical underlying symbol (e.g., NIFTY).
        exchange: Primary derivatives exchange code.
        expiry: Chain expiry in YYYY-MM-DD format.
        atm_strike: ATM strike at capture time.
        strike_step: Minimum strike increment.
        strike_window_strikes: Strikes included on each side of ATM.
        minimum_strike: Lowest strike in the slice.
        maximum_strike: Highest strike in the slice.
        lot_size: Exchange lot size.
        as_of: Decision timestamp for the snapshot.
        captured_at: Assembly completion timestamp; defaults to as_of.
        source: Snapshot origin.
        adapter_name: Producing adapter identifier.
        adapter_version: Optional adapter version.
        correlation_id: Optional pipeline correlation identifier.
        snapshot_id: Optional explicit snapshot ID; UUID generated when omitted.
        volatility: Optional volatility index observation.
        freshness_policy: Freshness policy overrides.
        validation_policy: Validation policy overrides.
        reference_time: Freshness reference clock; defaults to captured_at.
        strict: When True, warnings raise ``SnapshotBuildError``.

    Returns:
        Immutable validated market snapshot.

    Raises:
        SnapshotBuildError: On hard construction or strict validation failures.
        ValueError: When required timestamps are not timezone-aware.
    """
    if not _is_timezone_aware(as_of):
        raise ValueError("as_of must be timezone-aware.")

    resolved_captured_at = captured_at or as_of
    if not _is_timezone_aware(resolved_captured_at):
        raise ValueError("captured_at must be timezone-aware.")

    if not math.isfinite(underlying.last_price) or underlying.last_price <= 0:
        raise SnapshotBuildError("underlying.last_price must be finite and greater than zero.")

    sorted_contracts = _sort_contracts(contracts)
    if not sorted_contracts:
        raise SnapshotBuildError("contracts must be non-empty.")

    complete_pairs = _count_complete_pairs(sorted_contracts)
    metadata = OptionChainMetadata(
        underlying=underlying_symbol,
        exchange=exchange,
        expiry=expiry,
        atm_strike=atm_strike,
        strike_step=strike_step,
        strike_window_strikes=strike_window_strikes,
        minimum_strike=minimum_strike,
        maximum_strike=maximum_strike,
        lot_size=lot_size,
        contract_count=len(sorted_contracts),
        complete_pairs=complete_pairs,
    )
    option_chain = OptionChainSnapshot(metadata=metadata, contracts=sorted_contracts)

    provenance = SnapshotProvenance(
        snapshot_id=snapshot_id or str(uuid.uuid4()),
        schema_version=MARKET_SNAPSHOT_SCHEMA_VERSION,
        source=source,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        correlation_id=correlation_id,
        as_of=as_of,
        captured_at=resolved_captured_at,
        underlying_symbol=underlying_symbol,
        exchange=exchange,
    )

    provisional = MarketSnapshot(
        provenance=provenance,
        freshness=SnapshotFreshness(
            status=SnapshotFreshnessStatus.UNKNOWN,
            reference_time=reference_time or resolved_captured_at,
            observation_time=as_of,
            age_seconds=0.0,
            market_session_open=False,
            max_age_seconds=0.0,
            is_usable_for_live_decisions=False,
            reason="Pending freshness evaluation.",
        ),
        quality=SnapshotQuality(
            validation_status=SnapshotValidationStatus.INVALID,
            completeness_score=0.0,
            missing_quotes=0,
            inverted_markets=0,
            warnings=(),
            errors=(),
        ),
        underlying=underlying,
        volatility=volatility,
        option_chain=option_chain,
    )

    validation_result = validate_market_snapshot(
        provisional,
        policy=validation_policy,
    )
    if strict and validation_result.warnings:
        raise SnapshotBuildError(
            "Strict snapshot build rejected validation warnings: "
            + "; ".join(warning.message for warning in validation_result.warnings)
        )
    if validation_result.validation_status == SnapshotValidationStatus.INVALID:
        raise SnapshotBuildError(
            "Snapshot validation failed: "
            + "; ".join(error.message for error in validation_result.errors)
        )

    resolved_reference = reference_time or resolved_captured_at
    freshness = evaluate_snapshot_freshness(
        provisional,
        reference_time=resolved_reference,
        policy=freshness_policy,
    )

    quality = SnapshotQuality(
        validation_status=validation_result.validation_status,
        completeness_score=validation_result.completeness_score,
        expected_contract_count=strike_window_strikes * 2,
        missing_quotes=validation_result.missing_quotes,
        inverted_markets=validation_result.inverted_markets,
        warnings=validation_result.warnings,
        errors=validation_result.errors,
    )

    return MarketSnapshot(
        provenance=provenance,
        freshness=freshness,
        quality=quality,
        underlying=underlying,
        volatility=volatility,
        option_chain=option_chain,
    )


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat()


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value)
    else:
        raise SnapshotValidationError(
            f"Field '{field_name}' must be an ISO datetime string.",
            code=ERROR_DESERIALIZATION,
        )
    if not _is_timezone_aware(parsed):
        raise SnapshotValidationError(
            f"Field '{field_name}' must be timezone-aware.",
            code=ERROR_DESERIALIZATION,
        )
    return parsed


def _serialize_enum(value: Enum) -> str:
    return value.value


def _serialize_optional(value: Any) -> Any:
    if isinstance(value, datetime):
        return _serialize_datetime(value)
    if isinstance(value, Enum):
        return _serialize_enum(value)
    if isinstance(value, tuple):
        return [_serialize_dataclass_item(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return _serialize_dataclass_item(value)
    return value


def _serialize_dataclass_item(
    instance: Any,
    *,
    omit_nulls: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in fields(instance):
        value = getattr(instance, field.name)
        if value is None and omit_nulls:
            continue
        if hasattr(value, "__dataclass_fields__"):
            payload[field.name] = _serialize_dataclass_item(value, omit_nulls=omit_nulls)
        elif isinstance(value, Enum):
            payload[field.name] = _serialize_enum(value)
        elif isinstance(value, datetime):
            payload[field.name] = _serialize_datetime(value)
        elif isinstance(value, tuple):
            payload[field.name] = [
                _serialize_dataclass_item(item, omit_nulls=omit_nulls)
                if hasattr(item, "__dataclass_fields__")
                else _serialize_optional(item)
                for item in value
            ]
        else:
            payload[field.name] = value
    return payload


def to_dict(
    snapshot: MarketSnapshot,
    *,
    omit_nulls: bool = True,
) -> dict[str, Any]:
    """Serialize a snapshot to a dictionary.

    Args:
        snapshot: Snapshot to serialize.
        omit_nulls: When True, omit optional fields with None values.

    Returns:
        Dictionary representation including schema_version.
    """
    payload = _serialize_dataclass_item(snapshot, omit_nulls=omit_nulls)
    payload["schema_version"] = MARKET_SNAPSHOT_SCHEMA_VERSION
    return payload


def to_json(
    snapshot: MarketSnapshot,
    *,
    omit_nulls: bool = True,
) -> str:
    """Serialize a snapshot to a JSON string."""
    return json.dumps(to_dict(snapshot, omit_nulls=omit_nulls), indent=2)


def _parse_enum(enum_cls: type[Enum], value: Any, field_name: str) -> Enum:
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise SnapshotValidationError(
            f"Field '{field_name}' has invalid enum value: {value!r}.",
            code=ERROR_DESERIALIZATION,
        ) from exc


def _filter_known_fields(data: Mapping[str, Any], dataclass_type: type[Any]) -> dict[str, Any]:
    allowed = {field.name for field in fields(dataclass_type)}
    filtered: dict[str, Any] = {}
    for key, value in data.items():
        if key in allowed:
            filtered[key] = value
        else:
            logger.warning("Ignoring unknown field %r during deserialization.", key)
    return filtered


def _parse_error_records(items: Any) -> tuple[SnapshotErrorRecord, ...]:
    if not items:
        return ()
    records: list[SnapshotErrorRecord] = []
    for item in items:
        if not isinstance(item, dict):
            raise SnapshotValidationError(
                "Error records must be dictionaries.",
                code=ERROR_DESERIALIZATION,
            )
        records.append(
            SnapshotErrorRecord(
                code=str(item["code"]),
                message=str(item["message"]),
                field=item.get("field"),
            )
        )
    return tuple(records)


def _parse_warning_records(items: Any) -> tuple[SnapshotWarningRecord, ...]:
    if not items:
        return ()
    records: list[SnapshotWarningRecord] = []
    for item in items:
        if not isinstance(item, dict):
            raise SnapshotValidationError(
                "Warning records must be dictionaries.",
                code=ERROR_DESERIALIZATION,
            )
        records.append(
            SnapshotWarningRecord(
                code=str(item["code"]),
                message=str(item["message"]),
                field=item.get("field"),
            )
        )
    return tuple(records)


def _parse_underlying(data: Mapping[str, Any]) -> UnderlyingSnapshot:
    filtered = _filter_known_fields(data, UnderlyingSnapshot)
    if "quote_timestamp" in filtered and filtered["quote_timestamp"] is not None:
        filtered["quote_timestamp"] = _parse_datetime(
            filtered["quote_timestamp"],
            "underlying.quote_timestamp",
        )
    return UnderlyingSnapshot(**filtered)


def _parse_volatility(data: Mapping[str, Any] | None) -> VolatilitySnapshot | None:
    if data is None:
        return None
    filtered = _filter_known_fields(data, VolatilitySnapshot)
    if "quote_timestamp" in filtered and filtered["quote_timestamp"] is not None:
        filtered["quote_timestamp"] = _parse_datetime(
            filtered["quote_timestamp"],
            "volatility.quote_timestamp",
        )
    return VolatilitySnapshot(**filtered)


def _parse_contract(data: Mapping[str, Any]) -> OptionContractSnapshot:
    filtered = _filter_known_fields(data, OptionContractSnapshot)
    filtered["option_type"] = _parse_enum(
        OptionType,
        filtered["option_type"],
        "option_type",
    )
    if "quote_timestamp" in filtered and filtered["quote_timestamp"] is not None:
        filtered["quote_timestamp"] = _parse_datetime(
            filtered["quote_timestamp"],
            "option_chain.contracts.quote_timestamp",
        )
    return OptionContractSnapshot(**filtered)


def _parse_option_chain(data: Mapping[str, Any]) -> OptionChainSnapshot:
    metadata_raw = data.get("metadata")
    contracts_raw = data.get("contracts")
    if not isinstance(metadata_raw, dict):
        raise SnapshotValidationError(
            "option_chain.metadata must be a dictionary.",
            code=ERROR_DESERIALIZATION,
        )
    if not isinstance(contracts_raw, list):
        raise SnapshotValidationError(
            "option_chain.contracts must be a list.",
            code=ERROR_DESERIALIZATION,
        )
    metadata = OptionChainMetadata(**_filter_known_fields(metadata_raw, OptionChainMetadata))
    contracts = tuple(_parse_contract(item) for item in contracts_raw)
    return OptionChainSnapshot(metadata=metadata, contracts=contracts)


def _parse_provenance(data: Mapping[str, Any]) -> SnapshotProvenance:
    filtered = _filter_known_fields(data, SnapshotProvenance)
    filtered["source"] = _parse_enum(SnapshotSource, filtered["source"], "provenance.source")
    filtered["as_of"] = _parse_datetime(filtered["as_of"], "provenance.as_of")
    filtered["captured_at"] = _parse_datetime(filtered["captured_at"], "provenance.captured_at")
    return SnapshotProvenance(**filtered)


def _parse_freshness(data: Mapping[str, Any]) -> SnapshotFreshness:
    filtered = _filter_known_fields(data, SnapshotFreshness)
    filtered["status"] = _parse_enum(
        SnapshotFreshnessStatus,
        filtered["status"],
        "freshness.status",
    )
    filtered["reference_time"] = _parse_datetime(
        filtered["reference_time"],
        "freshness.reference_time",
    )
    filtered["observation_time"] = _parse_datetime(
        filtered["observation_time"],
        "freshness.observation_time",
    )
    return SnapshotFreshness(**filtered)


def _parse_quality(data: Mapping[str, Any]) -> SnapshotQuality:
    filtered = _filter_known_fields(data, SnapshotQuality)
    filtered["validation_status"] = _parse_enum(
        SnapshotValidationStatus,
        filtered["validation_status"],
        "quality.validation_status",
    )
    filtered["warnings"] = _parse_warning_records(filtered.get("warnings", ()))
    filtered["errors"] = _parse_error_records(filtered.get("errors", ()))
    return SnapshotQuality(**filtered)


def _validate_schema_version(version: str) -> None:
    if not isinstance(version, str):
        raise SnapshotValidationError(
            "schema_version must be a string.",
            code=ERROR_SCHEMA_UNSUPPORTED,
        )
    major = version.split(".", maxsplit=1)[0]
    expected_major = MARKET_SNAPSHOT_SCHEMA_VERSION.split(".", maxsplit=1)[0]
    if major != expected_major:
        raise SnapshotValidationError(
            f"Unsupported schema major version: {version}.",
            code=ERROR_SCHEMA_UNSUPPORTED,
        )


def from_dict(data: Mapping[str, Any]) -> MarketSnapshot:
    """Deserialize a snapshot dictionary.

    Args:
        data: Dictionary containing snapshot fields.

    Returns:
        Validated market snapshot.

    Raises:
        SnapshotValidationError: When schema or validation fails.
    """
    if not isinstance(data, Mapping):
        raise SnapshotValidationError(
            "Snapshot payload must be a mapping.",
            code=ERROR_DESERIALIZATION,
        )

    version = data.get("schema_version", MARKET_SNAPSHOT_SCHEMA_VERSION)
    _validate_schema_version(str(version))

    for unknown_key in data.keys():
        if unknown_key not in {
            "schema_version",
            "provenance",
            "freshness",
            "quality",
            "underlying",
            "volatility",
            "option_chain",
        }:
            logger.warning("Ignoring unknown root field %r during deserialization.", unknown_key)

    try:
        provenance = _parse_provenance(data["provenance"])
        freshness = _parse_freshness(data["freshness"])
        quality = _parse_quality(data["quality"])
        underlying = _parse_underlying(data["underlying"])
        volatility = _parse_volatility(data.get("volatility"))
        option_chain = _parse_option_chain(data["option_chain"])
    except KeyError as exc:
        raise SnapshotValidationError(
            f"Missing required snapshot field: {exc}.",
            code=ERROR_DESERIALIZATION,
        ) from exc
    except TypeError as exc:
        raise SnapshotValidationError(
            f"Invalid snapshot field types: {exc}.",
            code=ERROR_DESERIALIZATION,
        ) from exc

    snapshot = MarketSnapshot(
        provenance=provenance,
        freshness=freshness,
        quality=quality,
        underlying=underlying,
        volatility=volatility,
        option_chain=option_chain,
    )
    validation_result = validate_market_snapshot(snapshot)
    if validation_result.validation_status == SnapshotValidationStatus.INVALID:
        raise SnapshotValidationError(
            "Deserialized snapshot failed validation: "
            + "; ".join(error.message for error in validation_result.errors),
            code=ERROR_DESERIALIZATION,
        )
    return snapshot


def from_json(text: str) -> MarketSnapshot:
    """Deserialize a snapshot JSON string."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SnapshotValidationError(
            f"Invalid JSON payload: {exc.msg}.",
            code=ERROR_DESERIALIZATION,
        ) from exc
    if not isinstance(payload, dict):
        raise SnapshotValidationError(
            "JSON root must be an object.",
            code=ERROR_DESERIALIZATION,
        )
    return from_dict(payload)


def from_legacy_option_snapshot(
    data: Mapping[str, Any],
    *,
    source: SnapshotSource = SnapshotSource.REPLAY,
    adapter_name: str = "legacy_option_snapshot",
    correlation_id: str | None = None,
) -> MarketSnapshot:
    """Convert a legacy option snapshot dictionary into ``MarketSnapshot``.

    Supports shapes produced by ``market_data_engine.get_nifty_option_snapshot``
    and ``option_snapshot_engine`` persisted JSON.
    """
    if not isinstance(data, Mapping):
        raise SnapshotValidationError(
            "Legacy snapshot must be a mapping.",
            code=ERROR_DESERIALIZATION,
        )

    timestamp_raw = data.get("timestamp")
    if timestamp_raw is None:
        raise SnapshotValidationError(
            "Legacy snapshot requires 'timestamp'.",
            code=ERROR_DESERIALIZATION,
        )
    as_of = _parse_datetime(timestamp_raw, "timestamp")

    spot = data.get("spot")
    if spot is None:
        raise SnapshotValidationError(
            "Legacy snapshot requires 'spot'.",
            code=ERROR_DESERIALIZATION,
        )
    spot_price = float(spot)
    expiry = str(data.get("expiry", ""))
    atm = float(data.get("atm", spot_price))
    strike_step = float(data.get("strike_step", 50.0))
    options = data.get("options")
    if not isinstance(options, list) or not options:
        raise SnapshotValidationError(
            "Legacy snapshot requires non-empty 'options' list.",
            code=ERROR_DESERIALIZATION,
        )

    contracts: list[OptionContractSnapshot] = []
    strikes: list[float] = []
    for item in options:
        if not isinstance(item, dict):
            continue
        strike = float(item["strike"])
        strikes.append(strike)
        option_type = _parse_enum(OptionType, item.get("option_type", ""), "option_type")
        symbol = str(
            item.get(
                "symbol",
                f"NIFTY{expiry.replace('-', '')}{int(strike)}{option_type.value}",
            )
        )
        contracts.append(
            OptionContractSnapshot(
                underlying="NIFTY",
                exchange="NFO",
                tradingsymbol=symbol,
                expiry=expiry,
                strike=strike,
                option_type=option_type,
                lot_size=int(item.get("lot_size", 75)),
                ltp=float(item.get("price", item.get("ltp", 0.0))),
                bid=float(item.get("bid", 0.0)),
                ask=float(item.get("ask", 0.0)),
                volume=int(item.get("volume", 0)),
                open_interest=int(item.get("oi", item.get("open_interest", 0))),
                quote_timestamp=as_of,
            )
        )

    if not contracts:
        raise SnapshotValidationError(
            "Legacy snapshot options did not contain valid contracts.",
            code=ERROR_DESERIALIZATION,
        )

    minimum_strike = min(strikes)
    maximum_strike = max(strikes)
    strike_window = max(1, len({strike for strike in strikes}) // 2)

    underlying = UnderlyingSnapshot(
        symbol="NIFTY 50",
        exchange="NSE",
        quote_key="NSE:NIFTY 50",
        last_price=spot_price,
        quote_timestamp=as_of,
    )

    return build_market_snapshot(
        underlying=underlying,
        contracts=contracts,
        underlying_symbol="NIFTY",
        exchange="NFO",
        expiry=expiry,
        atm_strike=atm,
        strike_step=strike_step,
        strike_window_strikes=strike_window,
        minimum_strike=minimum_strike,
        maximum_strike=maximum_strike,
        lot_size=75,
        as_of=as_of,
        captured_at=as_of,
        source=source,
        adapter_name=adapter_name,
        correlation_id=correlation_id,
        reference_time=as_of,
    )


__all__ = [
    "MARKET_SNAPSHOT_SCHEMA_VERSION",
    "MarketSnapshot",
    "SnapshotProvenance",
    "SnapshotFreshness",
    "SnapshotQuality",
    "UnderlyingSnapshot",
    "VolatilitySnapshot",
    "OptionChainSnapshot",
    "OptionChainMetadata",
    "OptionContractSnapshot",
    "SnapshotFreshnessPolicy",
    "ValidationPolicy",
    "SnapshotValidationResult",
    "SnapshotFreshnessStatus",
    "SnapshotSource",
    "OptionType",
    "SnapshotValidationStatus",
    "SnapshotBuildError",
    "SnapshotValidationError",
    "build_market_snapshot",
    "validate_market_snapshot",
    "evaluate_snapshot_freshness",
    "is_live_trade_ready",
    "to_dict",
    "from_dict",
    "to_json",
    "from_json",
    "from_legacy_option_snapshot",
]
