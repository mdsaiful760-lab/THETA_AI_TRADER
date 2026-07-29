# ============================================================
# THETA AI TRADER — MARKET REGIME ENGINE
# ============================================================


class RegimeEngine:

    def __init__(self):

        self.regime = None
        self.confidence = 0
        self.reasons = []

    # --------------------------------------------------------
    # RESET
    # --------------------------------------------------------

    def reset(self):

        self.regime = None
        self.confidence = 0
        self.reasons = []

    # --------------------------------------------------------
    # ADX TREND STRENGTH
    # --------------------------------------------------------

    def analyze_adx(self, adx):

        adx = float(adx)

        if adx < 20:

            self.reasons.append(
                f"ADX {adx:.1f} indicates weak trend / range market"
            )

            return "RANGE"

        elif adx < 25:

            self.reasons.append(
                f"ADX {adx:.1f} indicates developing trend"
            )

            return "TRANSITION"

        else:

            self.reasons.append(
                f"ADX {adx:.1f} indicates strong trend"
            )

            return "TREND"


    # --------------------------------------------------------
    # EMA TREND DIRECTION
    # --------------------------------------------------------

    def analyze_ema(self, fast_ema, slow_ema):

        fast_ema = float(fast_ema)
        slow_ema = float(slow_ema)

        if fast_ema > slow_ema:

            self.reasons.append(
                f"Fast EMA {fast_ema:.2f} above Slow EMA {slow_ema:.2f} — bullish structure"
            )

            return "BULLISH"

        elif fast_ema < slow_ema:

            self.reasons.append(
                f"Fast EMA {fast_ema:.2f} below Slow EMA {slow_ema:.2f} — bearish structure"
            )

            return "BEARISH"

        else:

            self.reasons.append(
                f"Fast EMA {fast_ema:.2f} equal to Slow EMA {slow_ema:.2f} — neutral structure"
            )

            return "NEUTRAL"


    # --------------------------------------------------------
    # INDIA VIX VOLATILITY
    # --------------------------------------------------------

    def analyze_vix(self, vix):

        vix = float(vix)

        if vix < 12:

            self.reasons.append(
                f"India VIX {vix:.2f} indicates low volatility"
            )

            return "LOW_VOL"

        elif vix < 18:

            self.reasons.append(
                f"India VIX {vix:.2f} indicates normal volatility"
            )

            return "NORMAL_VOL"

        elif vix < 25:

            self.reasons.append(
                f"India VIX {vix:.2f} indicates high volatility"
            )

            return "HIGH_VOL"

        else:

            self.reasons.append(
                f"India VIX {vix:.2f} indicates extreme volatility"
            )

            return "EXTREME_VOL"


    # --------------------------------------------------------
    # ATR VOLATILITY
    # --------------------------------------------------------

    def analyze_atr(self, atr, spot_price):

        atr = float(atr)
        spot_price = float(spot_price)

        if spot_price <= 0:
            raise ValueError("Spot price must be greater than zero")

        atr_pct = (atr / spot_price) * 100

        if atr_pct < 0.5:

            self.reasons.append(
                f"ATR {atr_pct:.2f}% indicates low realized volatility"
            )

            return "LOW_ATR"

        elif atr_pct < 0.8:

            self.reasons.append(
                f"ATR {atr_pct:.2f}% indicates normal realized volatility"
            )

            return "NORMAL_ATR"

        elif atr_pct < 1.2:

            self.reasons.append(
                f"ATR {atr_pct:.2f}% indicates high realized volatility"
            )

            return "HIGH_ATR"

        else:

            self.reasons.append(
                f"ATR {atr_pct:.2f}% indicates extreme realized volatility"
            )

            return "EXTREME_ATR"

    # --------------------------------------------------------
    # PERCENTILE CALCULATION
    # --------------------------------------------------------

    def calculate_percentile(self, values, pct):

        if not values:
            raise ValueError(
                "No values provided for percentile calculation"
            )

        values = sorted(
            float(value)
            for value in values
        )

        index = (len(values) - 1) * pct

        lower = int(index)
        upper = min(
            lower + 1,
            len(values) - 1,
        )

        weight = index - lower

        return (
            values[lower] * (1 - weight)
            + values[upper] * weight
        )
    # --------------------------------------------------------
    # ADAPTIVE ATR VOLATILITY
    # --------------------------------------------------------

    def analyze_adaptive_atr(
        self,
        atr,
        spot_price,
        historical_atr_values,
    ):

        atr = float(atr)
        spot_price = float(spot_price)

        if spot_price <= 0:
            raise ValueError(
                "Spot price must be greater than zero"
            )

        if not historical_atr_values:
            raise ValueError(
                "Historical ATR values are required"
            )

        # Convert current ATR to percentage of spot
        atr_pct = (
            atr / spot_price
        ) * 100

        # Adaptive thresholds from historical distribution
        p25 = self.calculate_percentile(
            historical_atr_values,
            0.25,
        )

        p75 = self.calculate_percentile(
            historical_atr_values,
            0.75,
        )

        p95 = self.calculate_percentile(
            historical_atr_values,
            0.95,
        )

        if atr_pct <= p25:

            self.reasons.append(
                f"ATR {atr_pct:.4f}% is at/below "
                f"25th percentile {p25:.4f}% — low volatility"
            )

            return "LOW_ATR"

        elif atr_pct <= p75:

            self.reasons.append(
                f"ATR {atr_pct:.4f}% is between "
                f"25th and 75th percentile — normal volatility"
            )

            return "NORMAL_ATR"

        elif atr_pct <= p95:

            self.reasons.append(
                f"ATR {atr_pct:.4f}% is between "
                f"75th and 95th percentile — high volatility"
            )

            return "HIGH_ATR"

        else:

            self.reasons.append(
                f"ATR {atr_pct:.4f}% is above "
                f"95th percentile {p95:.4f}% — extreme volatility"
            )

            return "EXTREME_ATR"

    # --------------------------------------------------------
    # REGIME WARM-UP SAFETY CHECK
    # --------------------------------------------------------

    def check_warmup(
        self,
        candles,
        required_candles=15,
    ):
        """
        Check whether enough candles exist in the latest
        trading session for reliable regime analysis.
        """

        if not candles:
            return False, 0

        latest_date = candles[-1]["date"].date()

        session_candles = [
            candle
            for candle in candles
            if candle["date"].date() == latest_date
        ]

        candle_count = len(session_candles)

        regime_ready = (
            candle_count >= required_candles
        )

        return regime_ready, candle_count

    # --------------------------------------------------------
    # MASTER REGIME DETECTION
    # --------------------------------------------------------

    def detect_regime(
        self,
        adx,
        fast_ema,
        slow_ema,
        vix,
        atr,
        spot_price,
        historical_atr_values=None,
        candles=None,
    ):

        # Clear results from previous analysis
        self.reset()

        # ----------------------------------------------------
        # WARM-UP SAFETY STATUS
        # ----------------------------------------------------

        if candles:

            regime_ready, session_candle_count = self.check_warmup(
                candles,
                required_candles=15,
            )

        else:

            # Backward-compatible fallback when candles
            # are not supplied by the caller.
            regime_ready = True
            session_candle_count = None

        # Analyze individual market components
        adx_state = self.analyze_adx(adx)

        ema_state = self.analyze_ema(
            fast_ema,
            slow_ema,
        )

        vix_state = self.analyze_vix(vix)

        # Use adaptive ATR when historical calibration
        # data is available. Otherwise use legacy ATR.
        if historical_atr_values:

            atr_state = self.analyze_adaptive_atr(
                atr=atr,
                spot_price=spot_price,
                historical_atr_values=historical_atr_values,
            )

        else:

            atr_state = self.analyze_atr(
                atr,
                spot_price,
            )

        # ----------------------------------------------------
        # HIGH VOLATILITY REGIME
        # ----------------------------------------------------

        if (
            vix_state == "EXTREME_VOL"
            or atr_state == "EXTREME_ATR"
        ):
            self.regime = "HIGH_VOLATILITY"
            self.confidence = 90

        elif (
            vix_state == "HIGH_VOL"
            and atr_state in ("HIGH_ATR", "EXTREME_ATR")
        ):
            self.regime = "HIGH_VOLATILITY"
            self.confidence = 80

        # ----------------------------------------------------
        # RANGE-BOUND REGIME
        # ----------------------------------------------------

        elif (
            adx_state == "RANGE"
            and vix_state in ("LOW_VOL", "NORMAL_VOL")
            and atr_state in ("LOW_ATR", "NORMAL_ATR")
        ):
            self.regime = "RANGE_BOUND"
            self.confidence = 85

                # ----------------------------------------------------
        # BULLISH TREND REGIME
        # ----------------------------------------------------

        elif (
            adx_state == "TREND"
            and ema_state == "BULLISH"
        ):
            self.regime = "BULLISH_TREND"
            self.confidence = 85

        # ----------------------------------------------------
        # BEARISH TREND REGIME
        # ----------------------------------------------------

        elif (
            adx_state == "TREND"
            and ema_state == "BEARISH"
        ):
            self.regime = "BEARISH_TREND"
            self.confidence = 85

         # ----------------------------------------------------
        # TRANSITION / UNCERTAIN MARKET
        # ----------------------------------------------------

        elif adx_state == "TRANSITION":
            self.regime = "TRANSITION"
            self.confidence = 60

            self.reasons.append(
                "Market is transitioning between range and trend — avoid new trades"
            )

        # ----------------------------------------------------
        # UNCERTAIN / CONFLICTING MARKET
        # ----------------------------------------------------

        else:
            self.regime = "UNCERTAIN"
            self.confidence = 40

            self.reasons.append(
                "Market signals are conflicting or insufficient "
                "for a reliable regime classification"
            )

        # ----------------------------------------------------
        # FINAL TRADING PERMISSION
        # ----------------------------------------------------

        trading_permission = (
            "ALLOWED"
            if regime_ready
            else "NO NEW TRADE"
        )

        # Return complete regime analysis
        return {
            "regime": self.regime,
            "confidence": self.confidence,
            "adx_state": adx_state,
            "ema_state": ema_state,
            "vix_state": vix_state,
            "atr_state": atr_state,
            "regime_ready": regime_ready,
            "session_candle_count": session_candle_count,
            "trading_permission": trading_permission,
            "reasons": self.reasons.copy(),
        }