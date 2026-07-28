# ============================================================
# THETA AI TRADER — STRATEGY SELECTION ENGINE
# ============================================================


class StrategyEngine:
    """
    Selects a strategy family based on the output
    produced by the Regime Engine.

    IMPORTANT:
    This engine does NOT place orders.
    It only produces a strategy decision.
    """

    def __init__(self):
        self.strategy = None
        self.action = "WAIT"
        self.confidence = 0
        self.reasons = []

    # --------------------------------------------------------
    # RESET
    # --------------------------------------------------------

    def reset(self):
        self.strategy = None
        self.action = "WAIT"
        self.confidence = 0
        self.reasons = []

    # --------------------------------------------------------
    # VALIDATE REGIME RESULT
    # --------------------------------------------------------

    def validate_regime_result(self, regime_result):

        if not isinstance(regime_result, dict):
            raise ValueError(
                "Regime result must be a dictionary"
            )

        required_fields = [
            "regime",
            "confidence",
            "adx_state",
            "ema_state",
            "vix_state",
            "atr_state",
            "regime_ready",
            "trading_permission",
        ]

        missing_fields = [
            field
            for field in required_fields
            if field not in regime_result
        ]

        if missing_fields:
            raise ValueError(
                "Missing regime fields: "
                + ", ".join(missing_fields)
            )

    # --------------------------------------------------------
    # BLOCKED DECISION
    # --------------------------------------------------------

    def blocked_decision(self, reason):

        self.strategy = "NO_STRATEGY"
        self.action = "NO_TRADE"
        self.confidence = 100

        self.reasons.append(reason)

        return self.build_result()

    # --------------------------------------------------------
    # RANGE-BOUND MARKET
    # --------------------------------------------------------

    def handle_range_bound(
        self,
        ema_state,
        atr_state,
        vix_state,
    ):

        # Extreme volatility should never reach
        # a normal range-selling decision.
        if (
            atr_state == "EXTREME_ATR"
            or vix_state == "EXTREME_VOL"
        ):
            return self.blocked_decision(
                "Extreme volatility detected — "
                "range strategy blocked"
            )

        # ---------------------------------------------
        # LOW REALIZED VOLATILITY
        # ---------------------------------------------

        if atr_state == "LOW_ATR":

            self.strategy = "SHORT_STRANGLE"
            self.action = "EVALUATE"
            self.confidence = 80

            self.reasons.append(
                "Range-bound market with low realized "
                "volatility detected"
            )

            if ema_state == "BEARISH":
                self.reasons.append(
                    "EMA structure is bearish — "
                    "apply bearish strike-selection bias"
                )

            elif ema_state == "BULLISH":
                self.reasons.append(
                    "EMA structure is bullish — "
                    "apply bullish strike-selection bias"
                )

            else:
                self.reasons.append(
                    "EMA structure is neutral — "
                    "keep strike selection balanced"
                )

            return self.build_result()

        # ---------------------------------------------
        # NORMAL REALIZED VOLATILITY
        # ---------------------------------------------

        if atr_state == "NORMAL_ATR":

            self.strategy = "IRON_CONDOR"
            self.action = "EVALUATE"
            self.confidence = 75

            self.reasons.append(
                "Range-bound market with normal "
                "realized volatility detected"
            )

            self.reasons.append(
                "Defined-risk structure preferred "
                "before strike selection"
            )

            return self.build_result()

        # ---------------------------------------------
        # HIGH ATR INSIDE RANGE CLASSIFICATION
        # ---------------------------------------------

        self.strategy = "NO_STRATEGY"
        self.action = "WAIT"
        self.confidence = 70

        self.reasons.append(
            "Range classification exists but realized "
            "volatility is too high for current "
            "range-strategy rules"
        )

        return self.build_result()

    # --------------------------------------------------------
    # BULLISH TREND
    # --------------------------------------------------------

    def handle_bullish_trend(self):

        self.strategy = "BULL_PUT_SPREAD"
        self.action = "EVALUATE"
        self.confidence = 80

        self.reasons.append(
            "Strong bullish regime detected"
        )

        self.reasons.append(
            "Bull put spread selected as the "
            "defined-risk bullish premium-selling candidate"
        )

        return self.build_result()

    # --------------------------------------------------------
    # BEARISH TREND
    # --------------------------------------------------------

    def handle_bearish_trend(self):

        self.strategy = "BEAR_CALL_SPREAD"
        self.action = "EVALUATE"
        self.confidence = 80

        self.reasons.append(
            "Strong bearish regime detected"
        )

        self.reasons.append(
            "Bear call spread selected as the "
            "defined-risk bearish premium-selling candidate"
        )

        return self.build_result()

    # --------------------------------------------------------
    # BUILD RESULT
    # --------------------------------------------------------

    def build_result(self):

        return {
            "strategy": self.strategy,
            "action": self.action,
            "confidence": self.confidence,
            "reasons": self.reasons.copy(),
        }

    # --------------------------------------------------------
    # MASTER STRATEGY SELECTION
    # --------------------------------------------------------

    def select_strategy(self, regime_result):

        self.reset()

        self.validate_regime_result(
            regime_result
        )

        regime = regime_result["regime"]
        regime_confidence = float(
            regime_result["confidence"]
        )

        adx_state = regime_result["adx_state"]
        ema_state = regime_result["ema_state"]
        vix_state = regime_result["vix_state"]
        atr_state = regime_result["atr_state"]

        regime_ready = bool(
            regime_result["regime_ready"]
        )

        trading_permission = regime_result[
            "trading_permission"
        ]

        # ----------------------------------------------------
        # SAFETY GATE 1 — WARM-UP
        # ----------------------------------------------------

        if not regime_ready:
            return self.blocked_decision(
                "Regime Engine is still warming up"
            )

        # ----------------------------------------------------
        # SAFETY GATE 2 — REGIME PERMISSION
        # ----------------------------------------------------

        if trading_permission != "ALLOWED":
            return self.blocked_decision(
                "Regime Engine has not allowed "
                "new trade evaluation"
            )

        # ----------------------------------------------------
        # SAFETY GATE 3 — CONFIDENCE
        # ----------------------------------------------------

        if regime_confidence < 70:
            return self.blocked_decision(
                f"Regime confidence "
                f"{regime_confidence:.0f}% is below "
                f"minimum strategy threshold"
            )

        # ----------------------------------------------------
        # SAFETY GATE 4 — TRANSITION
        # ----------------------------------------------------

        if (
            regime == "TRANSITION"
            or adx_state == "TRANSITION"
        ):
            return self.blocked_decision(
                "Market is in transition — "
                "new strategy blocked"
            )

        # ----------------------------------------------------
        # SAFETY GATE 5 — HIGH VOLATILITY
        # ----------------------------------------------------

        if regime == "HIGH_VOLATILITY":
            return self.blocked_decision(
                "High-volatility regime detected — "
                "no automatic strategy selected"
            )

        # ----------------------------------------------------
        # RANGE-BOUND
        # ----------------------------------------------------

        if regime == "RANGE_BOUND":
            return self.handle_range_bound(
                ema_state=ema_state,
                atr_state=atr_state,
                vix_state=vix_state,
            )

        # ----------------------------------------------------
        # BULLISH TREND
        # ----------------------------------------------------

        if regime == "BULLISH_TREND":
            return self.handle_bullish_trend()

        # ----------------------------------------------------
        # BEARISH TREND
        # ----------------------------------------------------

        if regime == "BEARISH_TREND":
            return self.handle_bearish_trend()

        # ----------------------------------------------------
        # UNKNOWN / UNCLASSIFIED
        # ----------------------------------------------------

        return self.blocked_decision(
            f"Unsupported market regime: {regime}"
        )
