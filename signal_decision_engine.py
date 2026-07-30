# ============================================================
# THETA AI TRADER — SIGNAL DECISION ENGINE
# ============================================================


class SignalDecisionEngine:
    """
    Converts market intelligence into a trade-candidate decision.

    Inputs:
    - Market regime analysis
    - OI intelligence
    - Technical confirmation
    - Volatility state
    - Session state

    Possible decisions:
    - NO_TRADE
    - WAIT
    - BULLISH_SETUP
    - BEARISH_SETUP
    - RANGE_SETUP
    - BREAKOUT_SETUP

    IMPORTANT:
    This engine performs decision analysis only.

    It does NOT:
    - Select option strikes
    - Decide quantity
    - Place orders
    - Modify orders
    - Manage stop loss
    - Manage targets
    """

    def __init__(
        self,
        minimum_confidence="MEDIUM",
    ):
        self.minimum_confidence = (
            str(minimum_confidence)
            .upper()
            .strip()
        )

        self.confidence_rank = {
            "LOW": 1,
            "MEDIUM": 2,
            "HIGH": 3,
        }

        if (
            self.minimum_confidence
            not in self.confidence_rank
        ):
            raise ValueError(
                "minimum_confidence must be "
                "LOW, MEDIUM, or HIGH"
            )

    # --------------------------------------------------------
    # NORMALIZE TEXT
    # --------------------------------------------------------

    def _text(
        self,
        value,
        default="UNKNOWN",
    ):
        """
        Normalize text values used by upstream engines.
        """

        if value is None:
            return default

        value = (
            str(value)
            .upper()
            .strip()
        )

        if not value:
            return default

        return value

    # --------------------------------------------------------
    # CONFIDENCE CHECK
    # --------------------------------------------------------

    def _confidence_allowed(
        self,
        confidence,
    ):
        """
        Check whether regime confidence meets the configured
        minimum confidence requirement.
        """

        confidence = self._text(
            confidence,
            default="LOW",
        )

        current_rank = (
            self.confidence_rank.get(
                confidence,
                0,
            )
        )

        required_rank = (
            self.confidence_rank[
                self.minimum_confidence
            ]
        )

        return (
            current_rank
            >= required_rank
        )

    # --------------------------------------------------------
    # BUILD RESULT
    # --------------------------------------------------------

    def _build_result(
        self,
        decision,
        setup_valid,
        direction,
        confidence,
        permission,
        regime,
        base_regime,
        signal_conflict=False,
        reasons=None,
        safety_flags=None,
        bullish_confirmation=None,
        bearish_confirmation=None,
        confirmation_score=None,
        required_confirmation=None,
    ):
        """
        Build a consistent decision dictionary.
        """

        return {
            "decision": decision,

            "setup_valid": bool(
                setup_valid
            ),

            "direction": direction,

            "confidence": confidence,

            "trade_permission": (
                permission
            ),

            "regime": regime,

            "base_regime": (
                base_regime
            ),

            "signal_conflict": bool(
                signal_conflict
            ),

            "bullish_confirmation": (
                bullish_confirmation
            ),

            "bearish_confirmation": (
                bearish_confirmation
            ),

            "confirmation_score": (
                confirmation_score
            ),

            "required_confirmation": (
                required_confirmation
            ),

            "reasons": list(
                reasons
                or []
            ),

            "safety_flags": list(
                safety_flags
                or []
            ),
        }

    # --------------------------------------------------------
    # ANALYZE
    # --------------------------------------------------------

    def analyze(
        self,
        regime_analysis,
        oi_analysis=None,
        technical=None,
        volatility=None,
        session=None,
    ):
        """
        Produce a trade-candidate decision.

        Safety philosophy:

        BLOCK:
            Always NO_TRADE.

        CAUTION:
            Requires full directional confirmation from:
            - OI
            - Price trend
            - EMA structure

        ALLOW:
            Still requires sufficient confirmation.

        A bullish/bearish market regime alone is NOT enough
        to create a valid trade setup.
        """

        regime_analysis = (
            regime_analysis
            or {}
        )

        oi_analysis = (
            oi_analysis
            or {}
        )

        technical = (
            technical
            or {}
        )

        volatility = (
            volatility
            or {}
        )

        session = (
            session
            or {}
        )

        reasons = []
        safety_flags = []

        # ----------------------------------------------------
        # READ MARKET REGIME
        # ----------------------------------------------------

        regime = self._text(
            regime_analysis.get(
                "regime"
            )
        )

        base_regime = self._text(
            regime_analysis.get(
                "base_regime"
            )
        )

        confidence = self._text(
            regime_analysis.get(
                "regime_confidence"
            ),
            default="LOW",
        )

        preferred_direction = (
            self._text(
                regime_analysis.get(
                    "preferred_direction"
                ),
                default="NONE",
            )
        )

        permission = self._text(
            regime_analysis.get(
                "trade_permission"
            ),
            default="BLOCK",
        )

        entry_allowed = bool(
            regime_analysis.get(
                "entry_allowed",
                False,
            )
        )

        signal_conflict = bool(
            regime_analysis.get(
                "signal_conflict",
                False,
            )
        )

        # ----------------------------------------------------
        # BLOCKED / UNSAFE REGIMES
        # ----------------------------------------------------

        blocked_regimes = {
            "UNSTABLE",
            "EXPIRY_SPIKE_RISK",
            "EVENT_RISK",
            "CLOSED",
        }

        if permission == "BLOCK":
            safety_flags.append(
                "REGIME_PERMISSION_BLOCK"
            )

        if not entry_allowed:
            safety_flags.append(
                "ENTRY_NOT_ALLOWED"
            )

        if regime in blocked_regimes:
            safety_flags.append(
                "UNSAFE_MARKET_REGIME"
            )

        # ----------------------------------------------------
        # VOLATILITY SAFETY
        # ----------------------------------------------------

        spike_detected = bool(
            volatility.get(
                "spike_detected",
                False,
            )
        )

        abnormal_candle = bool(
            volatility.get(
                "abnormal_candle",
                False,
            )
        )

        rapid_move = bool(
            volatility.get(
                "rapid_move",
                False,
            )
        )

        if spike_detected:
            safety_flags.append(
                "ACTIVE_PRICE_SPIKE"
            )

        if abnormal_candle:
            safety_flags.append(
                "ABNORMAL_CANDLE"
            )

        if rapid_move:
            safety_flags.append(
                "RAPID_MOVE"
            )

        # ----------------------------------------------------
        # SESSION SAFETY
        # ----------------------------------------------------

        if session:

            market_open = bool(
                session.get(
                    "market_open",
                    True,
                )
            )

            new_entries_allowed = bool(
                session.get(
                    "new_entries_allowed",
                    True,
                )
            )

            if not market_open:
                safety_flags.append(
                    "MARKET_CLOSED"
                )

            if not new_entries_allowed:
                safety_flags.append(
                    "SESSION_ENTRY_BLOCK"
                )

        # ----------------------------------------------------
        # HARD SAFETY RESULT
        # ----------------------------------------------------

        if safety_flags:

            reasons.append(
                "Safety layer blocked trade setup"
            )

            return self._build_result(
                decision="NO_TRADE",

                setup_valid=False,

                direction="NONE",

                confidence=confidence,

                permission=permission,

                regime=regime,

                base_regime=base_regime,

                signal_conflict=(
                    signal_conflict
                ),

                reasons=reasons,

                safety_flags=(
                    safety_flags
                ),
            )

        # ----------------------------------------------------
        # CONFIDENCE FILTER
        # ----------------------------------------------------

        if not self._confidence_allowed(
            confidence
        ):

            reasons.append(
                "Regime confidence below "
                "required level"
            )

            return self._build_result(
                decision="WAIT",

                setup_valid=False,

                direction="NONE",

                confidence=confidence,

                permission=permission,

                regime=regime,

                base_regime=base_regime,

                signal_conflict=(
                    signal_conflict
                ),

                reasons=reasons,

                safety_flags=[],
            )

        # ----------------------------------------------------
        # READ OI + TECHNICAL DIRECTION
        # ----------------------------------------------------

        oi_direction = self._text(
            oi_analysis.get(
                "oi_directional_bias"
            ),
            default="MIXED",
        )

        price_trend = self._text(
            technical.get(
                "price_trend"
            )
        )

        ema_structure = self._text(
            technical.get(
                "ema_structure"
            )
        )

        vwap_position = self._text(
            technical.get(
                "vwap_position"
            )
        )

        # ----------------------------------------------------
        # CONFIRMATION SCORES
        # ----------------------------------------------------

        bullish_confirmation = 0
        bearish_confirmation = 0

        if oi_direction == "BULLISH":
            bullish_confirmation += 1

        elif oi_direction == "BEARISH":
            bearish_confirmation += 1

        if price_trend == "BULLISH":
            bullish_confirmation += 1

        elif price_trend == "BEARISH":
            bearish_confirmation += 1

        if ema_structure == "BULLISH":
            bullish_confirmation += 1

        elif ema_structure == "BEARISH":
            bearish_confirmation += 1

        # VWAP is supporting evidence.
        if vwap_position == "ABOVE":
            bullish_confirmation += 0.5

        elif vwap_position == "BELOW":
            bearish_confirmation += 0.5

        # ----------------------------------------------------
        # REQUIRED CONFIRMATION
        # ----------------------------------------------------

        required_confirmation = 2.0

        if permission == "CAUTION":

            required_confirmation = 3.0

            reasons.append(
                "CAUTION permission requires "
                "stronger confirmation"
            )

        # ----------------------------------------------------
        # CONFLICT SAFETY
        # ----------------------------------------------------

        if signal_conflict:

            reasons.append(
                "OI and technical evidence "
                "are conflicting"
            )

            # A conflict does not automatically mean
            # NO_TRADE because MarketRegimeEngine already
            # downgraded it to CAUTION.
            #
            # However, full directional confirmation is
            # required before a setup can pass.
            required_confirmation = max(
                required_confirmation,
                3.0,
            )

        # ----------------------------------------------------
        # BULLISH SETUP
        # ----------------------------------------------------

        if (
            preferred_direction == "BULLISH"
            and bullish_confirmation
            >= required_confirmation
        ):

            if base_regime == "BREAKOUT_BULLISH":
                decision = (
                    "BREAKOUT_SETUP"
                )

                reasons.append(
                    "Bullish breakout regime "
                    "confirmed"
                )

            else:
                decision = (
                    "BULLISH_SETUP"
                )

                reasons.append(
                    "Bullish market regime "
                    "confirmed"
                )

            return self._build_result(
                decision=decision,

                setup_valid=True,

                direction="BULLISH",

                confidence=confidence,

                permission=permission,

                regime=regime,

                base_regime=base_regime,

                signal_conflict=(
                    signal_conflict
                ),

                reasons=reasons,

                safety_flags=[],

                bullish_confirmation=(
                    bullish_confirmation
                ),

                bearish_confirmation=(
                    bearish_confirmation
                ),

                confirmation_score=(
                    bullish_confirmation
                ),

                required_confirmation=(
                    required_confirmation
                ),
            )

        # ----------------------------------------------------
        # BEARISH SETUP
        # ----------------------------------------------------

        if (
            preferred_direction == "BEARISH"
            and bearish_confirmation
            >= required_confirmation
        ):

            if base_regime in (
                "BREAKOUT_BEARISH",
                "BREAKDOWN_BEARISH",
            ):
                decision = (
                    "BREAKOUT_SETUP"
                )

                reasons.append(
                    "Bearish breakout/breakdown "
                    "regime confirmed"
                )

            else:
                decision = (
                    "BEARISH_SETUP"
                )

                reasons.append(
                    "Bearish market regime "
                    "confirmed"
                )

            return self._build_result(
                decision=decision,

                setup_valid=True,

                direction="BEARISH",

                confidence=confidence,

                permission=permission,

                regime=regime,

                base_regime=base_regime,

                signal_conflict=(
                    signal_conflict
                ),

                reasons=reasons,

                safety_flags=[],

                bullish_confirmation=(
                    bullish_confirmation
                ),

                bearish_confirmation=(
                    bearish_confirmation
                ),

                confirmation_score=(
                    bearish_confirmation
                ),

                required_confirmation=(
                    required_confirmation
                ),
            )

        # ----------------------------------------------------
        # RANGE / NON-DIRECTIONAL SETUP
        # ----------------------------------------------------

        if (
            preferred_direction
            in (
                "NON_DIRECTIONAL",
                "RANGE",
            )
            and base_regime
            in (
                "RANGE_BOUND",
                "RANGE",
            )
            and permission == "ALLOW"
            and not signal_conflict
        ):

            reasons.append(
                "Stable range regime detected"
            )

            return self._build_result(
                decision="RANGE_SETUP",

                setup_valid=True,

                direction=(
                    "NON_DIRECTIONAL"
                ),

                confidence=confidence,

                permission=permission,

                regime=regime,

                base_regime=base_regime,

                signal_conflict=False,

                reasons=reasons,

                safety_flags=[],

                bullish_confirmation=(
                    bullish_confirmation
                ),

                bearish_confirmation=(
                    bearish_confirmation
                ),

                required_confirmation=(
                    required_confirmation
                ),
            )

        # ----------------------------------------------------
        # WAIT — INSUFFICIENT CONFIRMATION
        # ----------------------------------------------------

        reasons.append(
            "Current evidence is insufficient "
            "for a trade setup"
        )

        return self._build_result(
            decision="WAIT",

            setup_valid=False,

            direction="NONE",

            confidence=confidence,

            permission=permission,

            regime=regime,

            base_regime=base_regime,

            signal_conflict=(
                signal_conflict
            ),

            reasons=reasons,

            safety_flags=[],

            bullish_confirmation=(
                bullish_confirmation
            ),

            bearish_confirmation=(
                bearish_confirmation
            ),

            required_confirmation=(
                required_confirmation
            ),
        )