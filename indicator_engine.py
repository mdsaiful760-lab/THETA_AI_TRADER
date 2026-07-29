# ============================================================
# THETA AI TRADER — TECHNICAL INDICATOR ENGINE
# ============================================================

import math


class IndicatorEngine:
    """
    Calculates technical indicators from OHLC candle data.

    This engine performs calculations only.
    It does NOT fetch market data.
    It does NOT place orders.
    """

    def __init__(
        self,
        fast_ema_period=5,
        slow_ema_period=21,
        rsi_period=14,
        adx_period=14,
        atr_period=14,
    ):
        self.fast_ema_period = int(fast_ema_period)
        self.slow_ema_period = int(slow_ema_period)
        self.rsi_period = int(rsi_period)
        self.adx_period = int(adx_period)
        self.atr_period = int(atr_period)

        periods = (
            self.fast_ema_period,
            self.slow_ema_period,
            self.rsi_period,
            self.adx_period,
            self.atr_period,
        )

        if any(period <= 0 for period in periods):
            raise ValueError(
                "Indicator periods must be greater than zero"
            )

    # --------------------------------------------------------
    # CANDLE VALIDATION
    # --------------------------------------------------------

    def validate_candles(self, candles):
        """
        Validate OHLC candle structure.
        """

        if not candles:
            raise ValueError(
                "No candles provided"
            )

        required_fields = (
            "open",
            "high",
            "low",
            "close",
        )

        for candle in candles:

            for field in required_fields:

                if field not in candle:
                    raise ValueError(
                        f"Candle missing required field: {field}"
                    )

            open_price = float(candle["open"])
            high_price = float(candle["high"])
            low_price = float(candle["low"])
            close_price = float(candle["close"])

            if min(
                open_price,
                high_price,
                low_price,
                close_price,
            ) <= 0:
                raise ValueError(
                    "Candle prices must be greater than zero"
                )

            if high_price < low_price:
                raise ValueError(
                    "Candle high cannot be below candle low"
                )

        return True



    # --------------------------------------------------------
    # EXPONENTIAL MOVING AVERAGE
    # --------------------------------------------------------

    def calculate_ema(
        self,
        values,
        period,
    ):
        """
        Calculate the latest Exponential Moving Average.

        Uses SMA as the initial EMA seed.
        """

        period = int(period)

        if period <= 0:
            raise ValueError(
                "EMA period must be greater than zero"
            )

        if not values:
            raise ValueError(
                "No values provided for EMA calculation"
            )

        values = [
            float(value)
            for value in values
        ]

        if len(values) < period:
            raise ValueError(
                f"At least {period} values are required "
                f"for EMA calculation"
            )

        # Initial EMA = SMA of first 'period' values
        ema = (
            sum(values[:period])
            / period
        )

        multiplier = (
            2.0 / (period + 1)
        )

        # Continue EMA calculation through
        # remaining values.
        for value in values[period:]:

            ema = (
                (value - ema)
                * multiplier
                + ema
            )

        return ema



    # --------------------------------------------------------
    # RELATIVE STRENGTH INDEX (RSI)
    # --------------------------------------------------------

    def calculate_rsi(
        self,
        values,
        period=None,
    ):
        """
        Calculate the latest RSI using Wilder's smoothing.
        """

        if period is None:
            period = self.rsi_period

        period = int(period)

        if period <= 0:
            raise ValueError(
                "RSI period must be greater than zero"
            )

        if not values:
            raise ValueError(
                "No values provided for RSI calculation"
            )

        values = [
            float(value)
            for value in values
        ]

        if len(values) < period + 1:
            raise ValueError(
                f"At least {period + 1} values are required "
                f"for RSI calculation"
            )

        gains = []
        losses = []

        for index in range(1, len(values)):

            change = (
                values[index]
                - values[index - 1]
            )

            gains.append(
                max(change, 0.0)
            )

            losses.append(
                max(-change, 0.0)
            )

        average_gain = (
            sum(gains[:period])
            / period
        )

        average_loss = (
            sum(losses[:period])
            / period
        )

        for index in range(
            period,
            len(gains),
        ):

            average_gain = (
                (
                    average_gain
                    * (period - 1)
                )
                + gains[index]
            ) / period

            average_loss = (
                (
                    average_loss
                    * (period - 1)
                )
                + losses[index]
            ) / period

        if average_loss == 0:

            if average_gain == 0:
                return 50.0

            return 100.0

        relative_strength = (
            average_gain
            / average_loss
        )

        rsi = (
            100.0
            - (
                100.0
                / (1.0 + relative_strength)
            )
        )

        return rsi



    # --------------------------------------------------------
    # AVERAGE TRUE RANGE (ATR)
    # --------------------------------------------------------

    def calculate_atr(
        self,
        candles,
        period=None,
    ):
        """
        Calculate the latest ATR using Wilder's smoothing.
        """

        if period is None:
            period = self.atr_period

        period = int(period)

        if period <= 0:
            raise ValueError(
                "ATR period must be greater than zero"
            )

        self.validate_candles(candles)

        if len(candles) < period + 1:
            raise ValueError(
                f"At least {period + 1} candles are required "
                f"for ATR calculation"
            )

        true_ranges = []

        for index in range(1, len(candles)):

            current_high = float(
                candles[index]["high"]
            )

            current_low = float(
                candles[index]["low"]
            )

            previous_close = float(
                candles[index - 1]["close"]
            )

            true_range = max(
                current_high - current_low,
                abs(
                    current_high
                    - previous_close
                ),
                abs(
                    current_low
                    - previous_close
                ),
            )

            true_ranges.append(
                true_range
            )

        # Initial ATR = average of first
        # 'period' true ranges.
        atr = (
            sum(true_ranges[:period])
            / period
        )

        # Wilder smoothing
        for true_range in true_ranges[period:]:

            atr = (
                (
                    atr
                    * (period - 1)
                )
                + true_range
            ) / period

        return atr



    # --------------------------------------------------------
    # AVERAGE DIRECTIONAL INDEX (ADX)
    # --------------------------------------------------------

    def calculate_adx(
        self,
        candles,
        period=None,
    ):
        """
        Calculate the latest ADX using Wilder's method.

        ADX measures trend strength, not direction.
        """

        if period is None:
            period = self.adx_period

        period = int(period)

        if period <= 0:
            raise ValueError(
                "ADX period must be greater than zero"
            )

        self.validate_candles(candles)

        minimum_candles = (
            (period * 2) + 1
        )

        if len(candles) < minimum_candles:
            raise ValueError(
                f"At least {minimum_candles} candles are "
                f"required for ADX calculation"
            )

        true_ranges = []
        plus_dm_values = []
        minus_dm_values = []

        for index in range(
            1,
            len(candles),
        ):

            current_high = float(
                candles[index]["high"]
            )

            current_low = float(
                candles[index]["low"]
            )

            previous_high = float(
                candles[index - 1]["high"]
            )

            previous_low = float(
                candles[index - 1]["low"]
            )

            previous_close = float(
                candles[index - 1]["close"]
            )

            true_range = max(
                current_high - current_low,
                abs(
                    current_high
                    - previous_close
                ),
                abs(
                    current_low
                    - previous_close
                ),
            )

            upward_move = (
                current_high
                - previous_high
            )

            downward_move = (
                previous_low
                - current_low
            )

            plus_dm = (
                upward_move
                if (
                    upward_move > downward_move
                    and upward_move > 0
                )
                else 0.0
            )

            minus_dm = (
                downward_move
                if (
                    downward_move > upward_move
                    and downward_move > 0
                )
                else 0.0
            )

            true_ranges.append(
                true_range
            )

            plus_dm_values.append(
                plus_dm
            )

            minus_dm_values.append(
                minus_dm
            )

        smoothed_tr = sum(
            true_ranges[:period]
        )

        smoothed_plus_dm = sum(
            plus_dm_values[:period]
        )

        smoothed_minus_dm = sum(
            minus_dm_values[:period]
        )

        dx_values = []

        for index in range(
            period,
            len(true_ranges),
        ):

            if index > period:

                smoothed_tr = (
                    smoothed_tr
                    - (
                        smoothed_tr
                        / period
                    )
                    + true_ranges[index]
                )

                smoothed_plus_dm = (
                    smoothed_plus_dm
                    - (
                        smoothed_plus_dm
                        / period
                    )
                    + plus_dm_values[index]
                )

                smoothed_minus_dm = (
                    smoothed_minus_dm
                    - (
                        smoothed_minus_dm
                        / period
                    )
                    + minus_dm_values[index]
                )

            if smoothed_tr == 0:
                dx_values.append(0.0)
                continue

            plus_di = (
                100.0
                * smoothed_plus_dm
                / smoothed_tr
            )

            minus_di = (
                100.0
                * smoothed_minus_dm
                / smoothed_tr
            )

            di_sum = (
                plus_di
                + minus_di
            )

            if di_sum == 0:

                dx = 0.0

            else:

                dx = (
                    100.0
                    * abs(
                        plus_di
                        - minus_di
                    )
                    / di_sum
                )

            dx_values.append(
                dx
            )

        if len(dx_values) < period:
            raise ValueError(
                "Insufficient DX values for ADX calculation"
            )

        adx = (
            sum(dx_values[:period])
            / period
        )

        for dx in dx_values[period:]:

            adx = (
                (
                    adx
                    * (period - 1)
                )
                + dx
            ) / period

        return adx



    # --------------------------------------------------------
    # PRICE MOMENTUM
    # --------------------------------------------------------

    def calculate_momentum(
        self,
        values,
        period=10,
    ):
        """
        Calculate percentage price momentum.

        Positive value = upward momentum.
        Negative value = downward momentum.
        Zero = no momentum.
        """

        period = int(period)

        if period <= 0:
            raise ValueError(
                "Momentum period must be greater than zero"
            )

        if not values:
            raise ValueError(
                "No values provided for momentum calculation"
            )

        values = [
            float(value)
            for value in values
        ]

        if len(values) < period + 1:
            raise ValueError(
                f"At least {period + 1} values are required "
                f"for momentum calculation"
            )

        current_price = values[-1]

        previous_price = values[
            -(period + 1)
        ]

        if previous_price <= 0:
            raise ValueError(
                "Previous price must be greater than zero"
            )

        momentum = (
            (
                current_price
                - previous_price
            )
            / previous_price
        ) * 100.0

        return momentum



    # --------------------------------------------------------
    # COMBINED TECHNICAL ANALYSIS
    # --------------------------------------------------------

    def analyze(
        self,
        candles,
        momentum_period=10,
    ):
        """
        Calculate all core technical indicators from candles.

        Returns a clean dictionary that can be consumed
        by the Regime Engine and other system components.
        """

        self.validate_candles(candles)

        closes = [
            float(candle["close"])
            for candle in candles
        ]

        fast_ema = self.calculate_ema(
            closes,
            self.fast_ema_period,
        )

        slow_ema = self.calculate_ema(
            closes,
            self.slow_ema_period,
        )

        rsi = self.calculate_rsi(
            closes,
            self.rsi_period,
        )

        atr = self.calculate_atr(
            candles,
            self.atr_period,
        )

        adx = self.calculate_adx(
            candles,
            self.adx_period,
        )

        momentum = self.calculate_momentum(
            closes,
            momentum_period,
        )

        latest_close = closes[-1]

        if fast_ema > slow_ema:
            ema_structure = "BULLISH"

        elif fast_ema < slow_ema:
            ema_structure = "BEARISH"

        else:
            ema_structure = "NEUTRAL"

        return {
            "close": latest_close,
            "fast_ema": fast_ema,
            "slow_ema": slow_ema,
            "ema_structure": ema_structure,
            "rsi": rsi,
            "atr": atr,
            "adx": adx,
            "momentum": momentum,
        }