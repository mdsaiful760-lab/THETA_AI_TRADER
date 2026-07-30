# ============================================================
# THETA AI TRADER — MARKET REGIME ENGINE
# ============================================================


class MarketRegimeEngine:
    """
    THETA AI TRADER market-regime intelligence layer.

    Purpose
    -------
    Convert multiple independent market observations into a
    normalized market regime and trade-permission decision.

    Current intelligence sources:
    - OI directional bias
    - OI confidence
    - OI chain structure
    - Support / resistance state
    - Current OI PCR
    - Near-ATM PCR
    - Spot position inside OI range
    - Price trend
    - EMA structure
    - RSI
    - VWAP position
    - Volatility state
    - Expiry-day awareness
    - Opening-session awareness
    - Spike-risk awareness

    Possible regimes:
    - TRENDING_BULLISH
    - TRENDING_BEARISH
    - RANGE_BOUND
    - BREAKOUT_BULLISH
    - BREAKOUT_BEARISH
    - HIGH_VOLATILITY
    - EXPIRY_SPIKE_RISK
    - UNSTABLE
    - MIXED
    - NO_TRADE

    Trade permissions:
    - ALLOW
    - CAUTION
    - BLOCK

    Important
    ---------
    This engine performs analysis only.

    It does NOT:
    - Place orders
    - Select option quantity
    - Modify positions
    - Exit positions
    - Send broker orders
    """

    VALID_OI_BIASES = {
        "BULLISH",
        "BEARISH",
        "RANGE",
        "MIXED",
        "UNKNOWN",
    }

    VALID_OI_CONFIDENCE = {
        "HIGH",
        "MEDIUM",
        "LOW",
        "UNKNOWN",
    }

    VALID_PRICE_TRENDS = {
        "BULLISH",
        "BEARISH",
        "SIDEWAYS",
        "MIXED",
        "UNKNOWN",
    }

    VALID_EMA_STRUCTURES = {
        "BULLISH",
        "BEARISH",
        "FLAT",
        "MIXED",
        "UNKNOWN",
    }

    VALID_VWAP_POSITIONS = {
        "ABOVE",
        "BELOW",
        "NEAR",
        "UNKNOWN",
    }

    VALID_VOLATILITY_STATES = {
        "LOW",
        "NORMAL",
        "HIGH",
        "EXTREME",
        "UNKNOWN",
    }

    def __init__(
        self,
        bullish_threshold=5,
        bearish_threshold=5,
        range_threshold=4,
        high_confidence_gap=3,
        medium_confidence_gap=2,
        opening_risk_minutes=15,
        expiry_risk_multiplier=1.25,
    ):
        self.bullish_threshold = int(
            bullish_threshold
        )

        self.bearish_threshold = int(
            bearish_threshold
        )

        self.range_threshold = int(
            range_threshold
        )

        self.high_confidence_gap = int(
            high_confidence_gap
        )

        self.medium_confidence_gap = int(
            medium_confidence_gap
        )

        self.opening_risk_minutes = int(
            opening_risk_minutes
        )

        self.expiry_risk_multiplier = float(
            expiry_risk_multiplier
        )

        if self.bullish_threshold <= 0:
            raise ValueError(
                "bullish_threshold must be greater than zero"
            )

        if self.bearish_threshold <= 0:
            raise ValueError(
                "bearish_threshold must be greater than zero"
            )

        if self.range_threshold <= 0:
            raise ValueError(
                "range_threshold must be greater than zero"
            )

        if self.opening_risk_minutes < 0:
            raise ValueError(
                "opening_risk_minutes cannot be negative"
            )

        if self.expiry_risk_multiplier < 1.0:
            raise ValueError(
                "expiry_risk_multiplier must be at least 1.0"
            )

    # --------------------------------------------------------
    # NORMALIZATION HELPERS
    # --------------------------------------------------------

    def _normalize_text(
        self,
        value,
        default="UNKNOWN",
    ):
        """
        Convert input into normalized uppercase text.
        """

        if value is None:
            return default

        value = str(
            value
        ).strip().upper()

        if not value:
            return default

        return value

    def _safe_float(
        self,
        value,
        default=None,
    ):
        """
        Safely convert a value to float.
        """

        if value is None:
            return default

        try:
            return float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):
            return default

    def _safe_int(
        self,
        value,
        default=0,
    ):
        """
        Safely convert a value to integer.
        """

        if value is None:
            return default

        try:
            return int(
                value
            )

        except (
            TypeError,
            ValueError,
        ):
            return default

    # --------------------------------------------------------
    # SCORE CONTAINER
    # --------------------------------------------------------

    def _new_scores(self):
        """
        Create a fresh evidence-score container.
        """

        return {
            "bullish": 0.0,
            "bearish": 0.0,
            "range": 0.0,
            "risk": 0.0,
        }

    # --------------------------------------------------------
    # OI EVIDENCE
    # --------------------------------------------------------

    def _score_oi(
        self,
        scores,
        reasons,
        oi_analysis,
    ):
        """
        Score Open Interest intelligence.
        """

        if not oi_analysis:
            reasons.append(
                "OI analysis unavailable"
            )

            scores["risk"] += 1.0

            return

        oi_bias = self._normalize_text(
            oi_analysis.get(
                "oi_directional_bias"
            )
        )

        oi_confidence = self._normalize_text(
            oi_analysis.get(
                "oi_confidence"
            )
        )

        chain_structure = self._normalize_text(
            oi_analysis.get(
                "chain_structure"
            )
        )

        resistance_state = self._normalize_text(
            oi_analysis.get(
                "resistance_state"
            )
        )

        support_state = self._normalize_text(
            oi_analysis.get(
                "support_state"
            )
        )

        # ----------------------------------------------------
        # OI DIRECTIONAL BIAS
        # ----------------------------------------------------

        confidence_weight = 1.0

        if oi_confidence == "HIGH":
            confidence_weight = 2.0

        elif oi_confidence == "MEDIUM":
            confidence_weight = 1.5

        elif oi_confidence == "LOW":
            confidence_weight = 1.0

        if oi_bias == "BULLISH":
            scores["bullish"] += (
                2.0
                * confidence_weight
            )

            reasons.append(
                "OI directional bias is bullish"
            )

        elif oi_bias == "BEARISH":
            scores["bearish"] += (
                2.0
                * confidence_weight
            )

            reasons.append(
                "OI directional bias is bearish"
            )

        elif oi_bias == "RANGE":
            scores["range"] += (
                2.0
                * confidence_weight
            )

            reasons.append(
                "OI directional bias indicates range"
            )

        elif oi_bias == "MIXED":
            scores["risk"] += 1.0

            reasons.append(
                "OI directional evidence is mixed"
            )

        # ----------------------------------------------------
        # CHAIN STRUCTURE
        # ----------------------------------------------------

        if chain_structure == "BULLISH_SHIFT":
            scores["bullish"] += 2.0

            reasons.append(
                "Option chain shows bullish shift"
            )

        elif chain_structure == "BEARISH_SHIFT":
            scores["bearish"] += 2.0

            reasons.append(
                "Option chain shows bearish shift"
            )

        elif chain_structure == "RANGE_BUILDING":
            scores["range"] += 2.0

            reasons.append(
                "Option chain is building a range"
            )

        elif chain_structure == "VOLATILE_UNWINDING":
            scores["risk"] += 2.0

            reasons.append(
                "Option chain shows volatile unwinding"
            )

        elif chain_structure == "MIXED":
            scores["risk"] += 1.0

        # ----------------------------------------------------
        # SUPPORT / RESISTANCE
        # ----------------------------------------------------

        if (
            resistance_state == "WEAKENING"
        ):
            scores["bullish"] += 1.0

        elif (
            resistance_state == "STRENGTHENING"
        ):
            scores["bearish"] += 1.0

        if support_state == "STRENGTHENING":
            scores["bullish"] += 1.0

        elif support_state == "WEAKENING":
            scores["bearish"] += 1.0

        # ----------------------------------------------------
        # PCR EVIDENCE
        # ----------------------------------------------------

        current_pcr = self._safe_float(
            oi_analysis.get(
                "current_oi_pcr"
            )
        )

        near_atm_pcr = self._safe_float(
            oi_analysis.get(
                "near_atm_pcr"
            )
        )

        if current_pcr is not None:

            if current_pcr >= 1.10:
                scores["bullish"] += 1.0

            elif current_pcr <= 0.90:
                scores["bearish"] += 1.0

            else:
                scores["range"] += 0.5

        if near_atm_pcr is not None:

            if near_atm_pcr >= 1.10:
                scores["bullish"] += 1.0

            elif near_atm_pcr <= 0.90:
                scores["bearish"] += 1.0

            else:
                scores["range"] += 0.5

    # --------------------------------------------------------
    # PRICE / TECHNICAL EVIDENCE
    # --------------------------------------------------------

    def _score_technical(
        self,
        scores,
        reasons,
        technical,
    ):
        """
        Score price / technical structure.
        """

        if not technical:
            reasons.append(
                "Technical confirmation unavailable"
            )

            return

        price_trend = self._normalize_text(
            technical.get(
                "price_trend"
            )
        )

        ema_structure = self._normalize_text(
            technical.get(
                "ema_structure"
            )
        )

        vwap_position = self._normalize_text(
            technical.get(
                "vwap_position"
            )
        )

        rsi = self._safe_float(
            technical.get(
                "rsi"
            )
        )

        # ----------------------------------------------------
        # PRICE TREND
        # ----------------------------------------------------

        if price_trend == "BULLISH":
            scores["bullish"] += 2.0

            reasons.append(
                "Price trend is bullish"
            )

        elif price_trend == "BEARISH":
            scores["bearish"] += 2.0

            reasons.append(
                "Price trend is bearish"
            )

        elif price_trend == "SIDEWAYS":
            scores["range"] += 2.0

            reasons.append(
                "Price structure is sideways"
            )

        elif price_trend == "MIXED":
            scores["risk"] += 0.5

        # ----------------------------------------------------
        # EMA STRUCTURE
        # ----------------------------------------------------

        if ema_structure == "BULLISH":
            scores["bullish"] += 1.5

        elif ema_structure == "BEARISH":
            scores["bearish"] += 1.5

        elif ema_structure == "FLAT":
            scores["range"] += 1.0

        elif ema_structure == "MIXED":
            scores["risk"] += 0.5

        # ----------------------------------------------------
        # VWAP
        # ----------------------------------------------------

        if vwap_position == "ABOVE":
            scores["bullish"] += 1.0

        elif vwap_position == "BELOW":
            scores["bearish"] += 1.0

        elif vwap_position == "NEAR":
            scores["range"] += 0.5

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        if rsi is not None:

            if 55.0 <= rsi <= 70.0:
                scores["bullish"] += 1.0

            elif 30.0 <= rsi <= 45.0:
                scores["bearish"] += 1.0

            elif 45.0 < rsi < 55.0:
                scores["range"] += 0.5

            elif rsi > 80.0:
                scores["risk"] += 1.0

                reasons.append(
                    "RSI is extremely elevated"
                )

            elif rsi < 20.0:
                scores["risk"] += 1.0

                reasons.append(
                    "RSI is extremely depressed"
                )

    # --------------------------------------------------------
    # BREAKOUT EVIDENCE
    # --------------------------------------------------------

    def _detect_breakout(
        self,
        scores,
        reasons,
        oi_analysis,
        technical,
    ):
        """
        Detect basic support/resistance breakout conditions.

        This is intentionally conservative.

        Later versions can add:
        - candle close confirmation
        - volume confirmation
        - ATR expansion
        - retest confirmation
        """

        breakout = "NONE"

        if not oi_analysis:
            return breakout

        spot = self._safe_float(
            technical.get(
                "spot"
            )
            if technical
            else None
        )

        if spot is None:
            return breakout

        resistance = self._safe_float(
            oi_analysis.get(
                "major_resistance_strike"
            )
        )

        support = self._safe_float(
            oi_analysis.get(
                "major_support_strike"
            )
        )

        resistance_state = (
            self._normalize_text(
                oi_analysis.get(
                    "resistance_state"
                )
            )
        )

        support_state = (
            self._normalize_text(
                oi_analysis.get(
                    "support_state"
                )
            )
        )

        if (
            resistance is not None
            and spot > resistance
            and resistance_state
            == "WEAKENING"
        ):
            breakout = (
                "BULLISH_BREAKOUT"
            )

            scores["bullish"] += 2.0

            reasons.append(
                "Spot is above weakening OI resistance"
            )

        elif (
            support is not None
            and spot < support
            and support_state
            == "WEAKENING"
        ):
            breakout = (
                "BEARISH_BREAKOUT"
            )

            scores["bearish"] += 2.0

            reasons.append(
                "Spot is below weakening OI support"
            )

        return breakout

    # --------------------------------------------------------
    # OI / TECHNICAL CONFLICT SAFETY
    # --------------------------------------------------------

    def _detect_signal_conflict(
        self,
        oi_analysis,
        technical,
    ):
        """
        Detect meaningful disagreement between OI direction
        and price/technical direction.

        A conflict does not automatically block trading.
        Instead it downgrades confidence and forces CAUTION,
        preventing a HIGH-confidence ALLOW decision when the
        two major evidence groups disagree.
        """

        oi_analysis = oi_analysis or {}
        technical = technical or {}

        oi_bias = self._normalize_text(
            oi_analysis.get(
                "oi_directional_bias"
            )
        )

        price_trend = self._normalize_text(
            technical.get(
                "price_trend"
            )
        )

        ema_structure = self._normalize_text(
            technical.get(
                "ema_structure"
            )
        )

        vwap_position = self._normalize_text(
            technical.get(
                "vwap_position"
            )
        )

        technical_bullish = 0
        technical_bearish = 0

        if price_trend == "BULLISH":
            technical_bullish += 2
        elif price_trend == "BEARISH":
            technical_bearish += 2

        if ema_structure == "BULLISH":
            technical_bullish += 1
        elif ema_structure == "BEARISH":
            technical_bearish += 1

        if vwap_position == "ABOVE":
            technical_bullish += 1
        elif vwap_position == "BELOW":
            technical_bearish += 1

        technical_direction = "UNKNOWN"

        if technical_bullish > technical_bearish:
            technical_direction = "BULLISH"
        elif technical_bearish > technical_bullish:
            technical_direction = "BEARISH"

        conflict_detected = (
            oi_bias in ("BULLISH", "BEARISH")
            and technical_direction in (
                "BULLISH",
                "BEARISH",
            )
            and oi_bias != technical_direction
        )

        return {
            "conflict_detected": conflict_detected,
            "oi_direction": oi_bias,
            "technical_direction": technical_direction,
        }

    # --------------------------------------------------------
    # VOLATILITY / SPIKE RISK
    # --------------------------------------------------------

    def _score_volatility(
        self,
        scores,
        reasons,
        volatility,
        is_expiry_day,
    ):
        """
        Evaluate volatility and spike risk.
        """

        volatility = (
            volatility
            or {}
        )

        volatility_state = (
            self._normalize_text(
                volatility.get(
                    "volatility_state"
                )
            )
        )

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

        risk_increment = 0.0

        if volatility_state == "HIGH":
            risk_increment += 2.0

            reasons.append(
                "Market volatility is high"
            )

        elif volatility_state == "EXTREME":
            risk_increment += 4.0

            reasons.append(
                "Market volatility is extreme"
            )

        if spike_detected:
            risk_increment += 3.0

            reasons.append(
                "Price spike detected"
            )

        if abnormal_candle:
            risk_increment += 2.0

            reasons.append(
                "Abnormally large candle detected"
            )

        if rapid_move:
            risk_increment += 2.0

            reasons.append(
                "Rapid market movement detected"
            )

        if (
            is_expiry_day
            and risk_increment > 0
        ):
            risk_increment *= (
                self.expiry_risk_multiplier
            )

            reasons.append(
                "Expiry-day multiplier applied to "
                "volatility risk"
            )

        scores["risk"] += (
            risk_increment
        )

        return {
            "volatility_state": (
                volatility_state
            ),
            "spike_detected": (
                spike_detected
            ),
            "abnormal_candle": (
                abnormal_candle
            ),
            "rapid_move": (
                rapid_move
            ),
            "volatility_risk_score": (
                risk_increment
            ),
        }

    # --------------------------------------------------------
    # SESSION RISK
    # --------------------------------------------------------

    def _score_session(
        self,
        scores,
        reasons,
        session,
    ):
        """
        Evaluate market-session conditions.
        """

        session = (
            session
            or {}
        )

        minutes_from_open = (
            self._safe_float(
                session.get(
                    "minutes_from_open"
                )
            )
        )

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

        opening_risk = False

        if not market_open:
            scores["risk"] += 10.0

            reasons.append(
                "Market session is closed"
            )

        if not new_entries_allowed:
            scores["risk"] += 10.0

            reasons.append(
                "Session rules prohibit new entries"
            )

        if (
            minutes_from_open is not None
            and minutes_from_open
            < self.opening_risk_minutes
        ):
            opening_risk = True

            scores["risk"] += 2.0

            reasons.append(
                "Market is inside opening risk window"
            )

        return {
            "market_open": (
                market_open
            ),
            "new_entries_allowed": (
                new_entries_allowed
            ),
            "minutes_from_open": (
                minutes_from_open
            ),
            "opening_risk": (
                opening_risk
            ),
        }

    # --------------------------------------------------------
    # REGIME CONFIDENCE
    # --------------------------------------------------------

    def _calculate_confidence(
        self,
        bullish_score,
        bearish_score,
        range_score,
    ):
        """
        Calculate regime confidence using score agreement.
        """

        scores = sorted(
            [
                float(
                    bullish_score
                ),
                float(
                    bearish_score
                ),
                float(
                    range_score
                ),
            ],
            reverse=True,
        )

        top_score = scores[0]
        second_score = scores[1]

        gap = (
            top_score
            - second_score
        )

        if (
            top_score >= 7.0
            and gap >= self.high_confidence_gap
        ):
            return "HIGH"

        if (
            top_score >= 4.0
            and gap >= self.medium_confidence_gap
        ):
            return "MEDIUM"

        return "LOW"

    # --------------------------------------------------------
    # DETERMINE BASE REGIME
    # --------------------------------------------------------

    def _determine_base_regime(
        self,
        scores,
        breakout,
    ):
        """
        Convert directional evidence into a market regime.
        """

        bullish = scores[
            "bullish"
        ]

        bearish = scores[
            "bearish"
        ]

        range_score = scores[
            "range"
        ]

        if breakout == "BULLISH_BREAKOUT":
            if bullish > bearish:
                return (
                    "BREAKOUT_BULLISH"
                )

        if breakout == "BEARISH_BREAKOUT":
            if bearish > bullish:
                return (
                    "BREAKOUT_BEARISH"
                )

        highest_score = max(
            bullish,
            bearish,
            range_score,
        )

        winners = sum([
            bullish == highest_score,
            bearish == highest_score,
            range_score == highest_score,
        ])

        if winners > 1:
            return "MIXED"

        if (
            bullish
            == highest_score
            and bullish
            >= self.bullish_threshold
        ):
            return (
                "TRENDING_BULLISH"
            )

        if (
            bearish
            == highest_score
            and bearish
            >= self.bearish_threshold
        ):
            return (
                "TRENDING_BEARISH"
            )

        if (
            range_score
            == highest_score
            and range_score
            >= self.range_threshold
        ):
            return (
                "RANGE_BOUND"
            )

        return "MIXED"

    # --------------------------------------------------------
    # TRADE PERMISSION
    # --------------------------------------------------------

    def _determine_trade_permission(
        self,
        regime,
        confidence,
        risk_score,
        is_expiry_day,
        volatility_info,
        session_info,
        signal_conflict=False,
    ):
        """
        Determine whether NEW trade entries should be
        permitted.

        Existing-position management will later be handled
        separately by the risk/execution engines.
        """

        hard_blocks = []

        if not session_info[
            "market_open"
        ]:
            hard_blocks.append(
                "MARKET_CLOSED"
            )

        if not session_info[
            "new_entries_allowed"
        ]:
            hard_blocks.append(
                "SESSION_ENTRY_BLOCK"
            )

        if volatility_info[
            "volatility_state"
        ] == "EXTREME":
            hard_blocks.append(
                "EXTREME_VOLATILITY"
            )

        if volatility_info[
            "spike_detected"
        ]:
            hard_blocks.append(
                "ACTIVE_PRICE_SPIKE"
            )

        if risk_score >= 8.0:
            hard_blocks.append(
                "EXCESSIVE_RISK_SCORE"
            )

        if hard_blocks:
            return {
                "trade_permission": (
                    "BLOCK"
                ),
                "entry_allowed": False,
                "permission_reasons": (
                    hard_blocks
                ),
            }

        caution_reasons = []

        if signal_conflict:
            caution_reasons.append(
                "OI_TECHNICAL_CONFLICT"
            )

        if confidence == "LOW":
            caution_reasons.append(
                "LOW_REGIME_CONFIDENCE"
            )

        if regime in (
            "MIXED",
            "UNSTABLE",
            "HIGH_VOLATILITY",
            "EXPIRY_SPIKE_RISK",
        ):
            caution_reasons.append(
                "UNFAVOURABLE_REGIME"
            )

        if session_info[
            "opening_risk"
        ]:
            caution_reasons.append(
                "OPENING_RISK_WINDOW"
            )

        if (
            volatility_info[
                "volatility_state"
            ]
            == "HIGH"
        ):
            caution_reasons.append(
                "HIGH_VOLATILITY"
            )

        if is_expiry_day:
            caution_reasons.append(
                "EXPIRY_DAY"
            )

        if risk_score >= 4.0:
            caution_reasons.append(
                "ELEVATED_RISK_SCORE"
            )

        if caution_reasons:
            return {
                "trade_permission": (
                    "CAUTION"
                ),
                "entry_allowed": True,
                "permission_reasons": (
                    caution_reasons
                ),
            }

        return {
            "trade_permission": (
                "ALLOW"
            ),
            "entry_allowed": True,
            "permission_reasons": [],
        }

    # --------------------------------------------------------
    # FINAL REGIME OVERRIDE
    # --------------------------------------------------------

    def _apply_risk_regime_override(
        self,
        base_regime,
        scores,
        is_expiry_day,
        volatility_info,
    ):
        """
        Risk conditions can override the directional regime.

        Example:
        Strong bullish evidence may exist during a violent
        expiry spike. The market may still be bullish, but
        the immediate trading regime should be classified
        as EXPIRY_SPIKE_RISK instead of blindly reporting
        TRENDING_BULLISH.
        """

        risk_score = scores[
            "risk"
        ]

        if (
            is_expiry_day
            and (
                volatility_info[
                    "spike_detected"
                ]
                or volatility_info[
                    "rapid_move"
                ]
                or volatility_info[
                    "abnormal_candle"
                ]
            )
        ):
            return (
                "EXPIRY_SPIKE_RISK"
            )

        if (
            volatility_info[
                "volatility_state"
            ]
            == "EXTREME"
        ):
            return (
                "HIGH_VOLATILITY"
            )

        if risk_score >= 8.0:
            return "UNSTABLE"

        return base_regime

    # --------------------------------------------------------
    # MAIN ANALYSIS
    # --------------------------------------------------------

    def analyze(
        self,
        oi_analysis=None,
        technical=None,
        volatility=None,
        session=None,
        is_expiry_day=False,
    ):
        """
        Run complete market-regime analysis.

        Parameters
        ----------
        oi_analysis:
            Output from OIEngine.analyze_chain().

        technical:
            Optional dictionary containing:
            {
                "spot": float,
                "price_trend": "BULLISH"/"BEARISH"/...,
                "ema_structure": "BULLISH"/"BEARISH"/...,
                "rsi": float,
                "vwap_position": "ABOVE"/"BELOW"/...
            }

        volatility:
            Optional dictionary containing:
            {
                "volatility_state":
                    "LOW"/"NORMAL"/"HIGH"/"EXTREME",
                "spike_detected": bool,
                "abnormal_candle": bool,
                "rapid_move": bool
            }

        session:
            Optional dictionary containing:
            {
                "market_open": bool,
                "new_entries_allowed": bool,
                "minutes_from_open": float
            }

        is_expiry_day:
            True when current trading session is expiry day.
        """

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

        is_expiry_day = bool(
            is_expiry_day
        )

        scores = (
            self._new_scores()
        )

        reasons = []

        # ----------------------------------------------------
        # OI INTELLIGENCE
        # ----------------------------------------------------

        self._score_oi(
            scores=scores,
            reasons=reasons,
            oi_analysis=oi_analysis,
        )

        # ----------------------------------------------------
        # TECHNICAL INTELLIGENCE
        # ----------------------------------------------------

        self._score_technical(
            scores=scores,
            reasons=reasons,
            technical=technical,
        )

        # ----------------------------------------------------
        # BREAKOUT DETECTION
        # ----------------------------------------------------

        breakout = (
            self._detect_breakout(
                scores=scores,
                reasons=reasons,
                oi_analysis=oi_analysis,
                technical=technical,
            )
        )

        # ----------------------------------------------------
        # VOLATILITY RISK
        # ----------------------------------------------------

        volatility_info = (
            self._score_volatility(
                scores=scores,
                reasons=reasons,
                volatility=volatility,
                is_expiry_day=is_expiry_day,
            )
        )

        # ----------------------------------------------------
        # SESSION RISK
        # ----------------------------------------------------

        session_info = (
            self._score_session(
                scores=scores,
                reasons=reasons,
                session=session,
            )
        )

        # ----------------------------------------------------
        # BASE MARKET REGIME
        # ----------------------------------------------------

        base_regime = (
            self._determine_base_regime(
                scores=scores,
                breakout=breakout,
            )
        )

        # ----------------------------------------------------
        # OI / TECHNICAL CONFLICT SAFETY
        # ----------------------------------------------------

        conflict_info = (
            self._detect_signal_conflict(
                oi_analysis=oi_analysis,
                technical=technical,
            )
        )

        signal_conflict = (
            conflict_info[
                "conflict_detected"
            ]
        )

        if signal_conflict:
            reasons.append(
                "OI and technical directional evidence conflict"
            )

        # ----------------------------------------------------
        # CONFIDENCE
        # ----------------------------------------------------

        regime_confidence = (
            self._calculate_confidence(
                bullish_score=scores[
                    "bullish"
                ],
                bearish_score=scores[
                    "bearish"
                ],
                range_score=scores[
                    "range"
                ],
            )
        )

        # Conflict safety: disagreement between the two
        # major evidence groups cannot remain HIGH confidence.
        if (
            signal_conflict
            and regime_confidence == "HIGH"
        ):
            regime_confidence = "MEDIUM"

        # ----------------------------------------------------
        # RISK OVERRIDE
        # ----------------------------------------------------

        final_regime = (
            self._apply_risk_regime_override(
                base_regime=base_regime,
                scores=scores,
                is_expiry_day=is_expiry_day,
                volatility_info=volatility_info,
            )
        )

        # ----------------------------------------------------
        # TRADE PERMISSION
        # ----------------------------------------------------

        permission = (
            self._determine_trade_permission(
                regime=final_regime,
                confidence=regime_confidence,
                risk_score=scores[
                    "risk"
                ],
                is_expiry_day=is_expiry_day,
                volatility_info=volatility_info,
                session_info=session_info,
                signal_conflict=signal_conflict,
            )
        )

        # ----------------------------------------------------
        # DIRECTION
        # ----------------------------------------------------

        preferred_direction = (
            "NONE"
        )

        if final_regime in (
            "TRENDING_BULLISH",
            "BREAKOUT_BULLISH",
        ):
            preferred_direction = (
                "BULLISH"
            )

        elif final_regime in (
            "TRENDING_BEARISH",
            "BREAKOUT_BEARISH",
        ):
            preferred_direction = (
                "BEARISH"
            )

        elif final_regime == "RANGE_BOUND":
            preferred_direction = (
                "NON_DIRECTIONAL"
            )

        # ----------------------------------------------------
        # FINAL RESULT
        # ----------------------------------------------------

        return {
            "regime": (
                final_regime
            ),

            "base_regime": (
                base_regime
            ),

            "regime_confidence": (
                regime_confidence
            ),

            "preferred_direction": (
                preferred_direction
            ),

            "trade_permission": (
                permission[
                    "trade_permission"
                ]
            ),

            "entry_allowed": (
                permission[
                    "entry_allowed"
                ]
            ),

            "permission_reasons": (
                permission[
                    "permission_reasons"
                ]
            ),

            "signal_conflict": (
                signal_conflict
            ),

            "oi_direction": (
                conflict_info[
                    "oi_direction"
                ]
            ),

            "technical_direction": (
                conflict_info[
                    "technical_direction"
                ]
            ),

            "bullish_score": round(
                scores[
                    "bullish"
                ],
                2,
            ),

            "bearish_score": round(
                scores[
                    "bearish"
                ],
                2,
            ),

            "range_score": round(
                scores[
                    "range"
                ],
                2,
            ),

            "risk_score": round(
                scores[
                    "risk"
                ],
                2,
            ),

            "breakout_state": (
                breakout
            ),

            "is_expiry_day": (
                is_expiry_day
            ),

            "volatility_state": (
                volatility_info[
                    "volatility_state"
                ]
            ),

            "spike_detected": (
                volatility_info[
                    "spike_detected"
                ]
            ),

            "abnormal_candle": (
                volatility_info[
                    "abnormal_candle"
                ]
            ),

            "rapid_move": (
                volatility_info[
                    "rapid_move"
                ]
            ),

            "opening_risk": (
                session_info[
                    "opening_risk"
                ]
            ),

            "market_open": (
                session_info[
                    "market_open"
                ]
            ),

            "new_entries_allowed": (
                session_info[
                    "new_entries_allowed"
                ]
            ),

            "reasons": (
                reasons
            ),
        }