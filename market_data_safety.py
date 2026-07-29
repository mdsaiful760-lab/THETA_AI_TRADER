# ============================================================
# THETA AI TRADER — MARKET DATA SAFETY
# ============================================================

from datetime import datetime, time


class MarketDataSafety:
    """
    Validates whether market data is safe to use.

    Responsibilities:
    - Validate candle timestamps
    - Detect stale market data
    - Distinguish market-open from market-closed periods
    - Block analytical decisions when required data is unsafe

    This module does NOT:
    - Generate trading signals
    - Select strategies
    - Place orders
    """

    def __init__(
        self,
        market_open=time(9, 15),
        market_close=time(15, 30),
        max_candle_age_minutes=10,
    ):
        self.market_open = market_open
        self.market_close = market_close

        self.max_candle_age_minutes = int(
            max_candle_age_minutes
        )

        if self.max_candle_age_minutes <= 0:
            raise ValueError(
                "Maximum candle age must be greater than zero"
            )

    # --------------------------------------------------------
    # MARKET SESSION CHECK
    # --------------------------------------------------------

    def is_market_open(
        self,
        current_time=None,
    ):
        """
        Determine whether the regular NSE market session
        is currently open.

        Weekend handling is included.
        Exchange holidays will be added separately.
        """

        if current_time is None:
            current_time = datetime.now().astimezone()

        # Monday = 0
        # Sunday = 6
        if current_time.weekday() >= 5:
            return False

        current_clock = current_time.time()

        return (
            self.market_open
            <= current_clock
            <= self.market_close
        )

    # --------------------------------------------------------
    # CANDLE FRESHNESS
    # --------------------------------------------------------

    def validate_candle_freshness(
        self,
        candle_timestamp,
        current_time=None,
    ):
        """
        Check whether the latest candle is sufficiently fresh.

        During market hours:
        stale candles are considered unsafe.

        Outside market hours:
        the latest completed session candle is allowed,
        but is explicitly marked MARKET_CLOSED.
        """

        if candle_timestamp is None:
            raise ValueError(
                "Candle timestamp is required"
            )

        if current_time is None:
            current_time = datetime.now().astimezone()

        # Ensure both timestamps can be compared safely.
        if (
            candle_timestamp.tzinfo is not None
            and current_time.tzinfo is None
        ):
            current_time = current_time.astimezone()

        elif (
            candle_timestamp.tzinfo is None
            and current_time.tzinfo is not None
        ):
            candle_timestamp = candle_timestamp.replace(
                tzinfo=current_time.tzinfo
            )

        age_seconds = (
            current_time
            - candle_timestamp
        ).total_seconds()

        age_minutes = (
            age_seconds / 60.0
        )

        if age_minutes < 0:
            return {
                "safe": False,
                "status": "FUTURE_TIMESTAMP",
                "age_minutes": age_minutes,
                "reason": (
                    "Candle timestamp is ahead of current time"
                ),
            }

        market_open = self.is_market_open(
            current_time
        )

        if market_open:

            if (
                age_minutes
                > self.max_candle_age_minutes
            ):
                return {
                    "safe": False,
                    "status": "STALE",
                    "age_minutes": age_minutes,
                    "reason": (
                        "Latest candle is too old "
                        "for live market analysis"
                    ),
                }

            return {
                "safe": True,
                "status": "FRESH",
                "age_minutes": age_minutes,
                "reason": (
                    "Latest candle is fresh "
                    "for live market analysis"
                ),
            }

        return {
            "safe": True,
            "status": "MARKET_CLOSED",
            "age_minutes": age_minutes,
            "reason": (
                "Market is closed; latest available "
                "candle may be used for analysis only"
            ),
        }