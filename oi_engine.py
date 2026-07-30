# ============================================================
# THETA AI TRADER — OPEN INTEREST INTELLIGENCE ENGINE
# ============================================================


class OIEngine:
    """
    Advanced Open Interest intelligence engine.

    Core responsibilities:
    - Price + OI activity classification
    - CE resistance interpretation
    - PE support interpretation
    - Absolute-OI weighted chain analysis
    - Major support/resistance identification
    - Current OI concentration analysis
    - Current OI PCR
    - Fresh OI addition/unwinding analysis
    - Spot location inside OI range
    - Near-ATM OI pressure
    - OI directional bias
    - Evidence-agreement confidence

    This engine performs analysis only.

    It does NOT:
    - Fetch broker data
    - Generate orders
    - Place orders
    - Decide trading quantity
    """

    def __init__(
        self,
        min_price_change_pct=0.05,
        min_oi_change_pct=1.0,
    ):
        self.min_price_change_pct = float(
            min_price_change_pct
        )

        self.min_oi_change_pct = float(
            min_oi_change_pct
        )

        if self.min_price_change_pct < 0:
            raise ValueError(
                "Minimum price change cannot be negative"
            )

        if self.min_oi_change_pct < 0:
            raise ValueError(
                "Minimum OI change cannot be negative"
            )

    # --------------------------------------------------------
    # PERCENTAGE CHANGE
    # --------------------------------------------------------

    def calculate_change_pct(
        self,
        previous_value,
        current_value,
    ):
        """
        Calculate percentage change between two values.
        """

        previous_value = float(
            previous_value
        )

        current_value = float(
            current_value
        )

        if previous_value <= 0:
            raise ValueError(
                "Previous value must be greater than zero"
            )

        return (
            (
                current_value
                - previous_value
            )
            / previous_value
        ) * 100.0

    # --------------------------------------------------------
    # SAFE RATIO
    # --------------------------------------------------------

    def _safe_ratio(
        self,
        numerator,
        denominator,
    ):
        """
        Return a ratio safely.

        Returns None when denominator is zero.
        """

        numerator = float(
            numerator
        )

        denominator = float(
            denominator
        )

        if denominator == 0:
            return None

        return (
            numerator
            / denominator
        )

    # --------------------------------------------------------
    # PRICE + OI CLASSIFICATION
    # --------------------------------------------------------

    def classify(
        self,
        previous_price,
        current_price,
        previous_oi,
        current_oi,
    ):
        """
        Classify price + OI participation.

        Price UP   + OI UP   = LONG_BUILDUP
        Price DOWN + OI UP   = SHORT_BUILDUP
        Price DOWN + OI DOWN = LONG_UNWINDING
        Price UP   + OI DOWN = SHORT_COVERING

        Small changes below configured thresholds
        are classified as NEUTRAL.
        """

        price_change_pct = (
            self.calculate_change_pct(
                previous_price,
                current_price,
            )
        )

        oi_change_pct = (
            self.calculate_change_pct(
                previous_oi,
                current_oi,
            )
        )

        meaningful_price_change = (
            abs(price_change_pct)
            >= self.min_price_change_pct
        )

        meaningful_oi_change = (
            abs(oi_change_pct)
            >= self.min_oi_change_pct
        )

        if not (
            meaningful_price_change
            and meaningful_oi_change
        ):
            classification = "NEUTRAL"

        elif (
            price_change_pct > 0
            and oi_change_pct > 0
        ):
            classification = "LONG_BUILDUP"

        elif (
            price_change_pct < 0
            and oi_change_pct > 0
        ):
            classification = "SHORT_BUILDUP"

        elif (
            price_change_pct < 0
            and oi_change_pct < 0
        ):
            classification = "LONG_UNWINDING"

        elif (
            price_change_pct > 0
            and oi_change_pct < 0
        ):
            classification = "SHORT_COVERING"

        else:
            classification = "NEUTRAL"

        return {
            "classification": classification,
            "price_change_pct": price_change_pct,
            "oi_change_pct": oi_change_pct,
            "previous_price": float(
                previous_price
            ),
            "current_price": float(
                current_price
            ),
            "previous_oi": float(
                previous_oi
            ),
            "current_oi": float(
                current_oi
            ),
        }

    # --------------------------------------------------------
    # OPTION-SIDE OI INTERPRETATION
    # --------------------------------------------------------

    def interpret_option_activity(
        self,
        option_type,
        classification,
    ):
        """
        Interpret OI activity from the perspective of
        NIFTY support and resistance.

        CE SHORT_BUILDUP:
            Resistance strengthening.

        CE SHORT_COVERING:
            Resistance weakening.

        PE SHORT_BUILDUP:
            Support strengthening.

        PE SHORT_COVERING:
            Support weakening.
        """

        option_type = str(
            option_type
        ).upper()

        classification = str(
            classification
        ).upper()

        if option_type not in (
            "CE",
            "PE",
        ):
            raise ValueError(
                "Option type must be CE or PE"
            )

        if (
            option_type == "CE"
            and classification == "SHORT_BUILDUP"
        ):
            interpretation = (
                "RESISTANCE_STRENGTHENING"
            )

        elif (
            option_type == "CE"
            and classification == "SHORT_COVERING"
        ):
            interpretation = (
                "RESISTANCE_WEAKENING"
            )

        elif (
            option_type == "PE"
            and classification == "SHORT_BUILDUP"
        ):
            interpretation = (
                "SUPPORT_STRENGTHENING"
            )

        elif (
            option_type == "PE"
            and classification == "SHORT_COVERING"
        ):
            interpretation = (
                "SUPPORT_WEAKENING"
            )

        else:
            interpretation = (
                "DIRECTIONAL_ACTIVITY"
            )

        return {
            "option_type": option_type,
            "classification": classification,
            "interpretation": interpretation,
        }

    # --------------------------------------------------------
    # CURRENT OPTION-CHAIN STRUCTURE
    # --------------------------------------------------------

    def analyze_current_oi(
        self,
        current_options,
        spot=None,
        atm=None,
    ):
        """
        Analyze absolute/current option-chain OI.

        This is different from T1 -> T2 delta OI.

        It identifies:
        - Highest CE OI
        - Highest PE OI
        - Major resistance
        - Major support
        - Total CE OI
        - Total PE OI
        - Current OI PCR
        - CE/PE concentration
        - Spot position inside support/resistance
        - Near-ATM CE/PE OI pressure
        """

        if not current_options:
            return None

        normalized = []

        for option in current_options:

            try:
                strike = float(
                    option["strike"]
                )

                option_type = str(
                    option["option_type"]
                ).upper()

                current_oi = float(
                    option.get(
                        "oi",
                        0,
                    ) or 0
                )

            except (
                KeyError,
                TypeError,
                ValueError,
            ):
                continue

            if option_type not in (
                "CE",
                "PE",
            ):
                continue

            if current_oi < 0:
                continue

            normalized.append({
                "strike": strike,
                "option_type": option_type,
                "oi": current_oi,
                "symbol": option.get(
                    "symbol"
                ),
            })

        if not normalized:
            return None

        ce_options = [
            row
            for row in normalized
            if row["option_type"] == "CE"
        ]

        pe_options = [
            row
            for row in normalized
            if row["option_type"] == "PE"
        ]

        total_ce_oi = sum(
            row["oi"]
            for row in ce_options
        )

        total_pe_oi = sum(
            row["oi"]
            for row in pe_options
        )

        # ----------------------------------------------------
        # MAJOR RESISTANCE / SUPPORT
        # ----------------------------------------------------

        major_resistance = None
        major_support = None

        if ce_options:
            major_resistance = max(
                ce_options,
                key=lambda row: row["oi"],
            )

        if pe_options:
            major_support = max(
                pe_options,
                key=lambda row: row["oi"],
            )

        major_resistance_strike = (
            major_resistance["strike"]
            if major_resistance
            else None
        )

        major_support_strike = (
            major_support["strike"]
            if major_support
            else None
        )

        major_resistance_oi = (
            major_resistance["oi"]
            if major_resistance
            else 0.0
        )

        major_support_oi = (
            major_support["oi"]
            if major_support
            else 0.0
        )

        # ----------------------------------------------------
        # CURRENT OI PCR
        # ----------------------------------------------------

        current_oi_pcr = (
            self._safe_ratio(
                total_pe_oi,
                total_ce_oi,
            )
        )

        # ----------------------------------------------------
        # OI CONCENTRATION
        # ----------------------------------------------------

        ce_concentration_pct = 0.0
        pe_concentration_pct = 0.0

        if total_ce_oi > 0:
            ce_concentration_pct = (
                major_resistance_oi
                / total_ce_oi
            ) * 100.0

        if total_pe_oi > 0:
            pe_concentration_pct = (
                major_support_oi
                / total_pe_oi
            ) * 100.0

        # ----------------------------------------------------
        # SPOT LOCATION
        # ----------------------------------------------------

        spot_value = None

        if spot is not None:
            try:
                spot_value = float(
                    spot
                )
            except (
                TypeError,
                ValueError,
            ):
                spot_value = None

        distance_to_support = None
        distance_to_resistance = None
        range_position = "UNKNOWN"

        if (
            spot_value is not None
            and major_support_strike is not None
        ):
            distance_to_support = (
                spot_value
                - major_support_strike
            )

        if (
            spot_value is not None
            and major_resistance_strike is not None
        ):
            distance_to_resistance = (
                major_resistance_strike
                - spot_value
            )

        if (
            spot_value is not None
            and major_support_strike is not None
            and major_resistance_strike is not None
        ):
            if (
                major_support_strike
                < major_resistance_strike
            ):
                range_width = (
                    major_resistance_strike
                    - major_support_strike
                )

                range_fraction = (
                    (
                        spot_value
                        - major_support_strike
                    )
                    / range_width
                )

                if spot_value < major_support_strike:
                    range_position = (
                        "BELOW_SUPPORT"
                    )

                elif spot_value > major_resistance_strike:
                    range_position = (
                        "ABOVE_RESISTANCE"
                    )

                elif range_fraction < 0.33:
                    range_position = (
                        "LOWER_THIRD"
                    )

                elif range_fraction > 0.67:
                    range_position = (
                        "UPPER_THIRD"
                    )

                else:
                    range_position = (
                        "MIDDLE"
                    )

            else:
                range_position = (
                    "OVERLAPPING_LEVELS"
                )

        # ----------------------------------------------------
        # ATM / NEAR-ATM OI PRESSURE
        # ----------------------------------------------------

        atm_value = None

        if atm is not None:
            try:
                atm_value = float(
                    atm
                )
            except (
                TypeError,
                ValueError,
            ):
                atm_value = None

        if (
            atm_value is None
            and spot_value is not None
        ):
            strikes = sorted({
                row["strike"]
                for row in normalized
            })

            if strikes:
                atm_value = min(
                    strikes,
                    key=lambda strike: abs(
                        strike
                        - spot_value
                    ),
                )

        near_atm_ce_oi = 0.0
        near_atm_pe_oi = 0.0

        if atm_value is not None:

            strikes = sorted({
                row["strike"]
                for row in normalized
            })

            strike_step = None

            if len(strikes) >= 2:
                positive_differences = [
                    strikes[index]
                    - strikes[index - 1]
                    for index in range(
                        1,
                        len(strikes),
                    )
                    if (
                        strikes[index]
                        - strikes[index - 1]
                    ) > 0
                ]

                if positive_differences:
                    strike_step = min(
                        positive_differences
                    )

            if strike_step is None:
                strike_step = 50.0

            near_atm_distance = (
                strike_step
            )

            for row in normalized:

                if (
                    abs(
                        row["strike"]
                        - atm_value
                    )
                    <= near_atm_distance
                ):
                    if (
                        row["option_type"]
                        == "CE"
                    ):
                        near_atm_ce_oi += (
                            row["oi"]
                        )

                    elif (
                        row["option_type"]
                        == "PE"
                    ):
                        near_atm_pe_oi += (
                            row["oi"]
                        )

        near_atm_pcr = (
            self._safe_ratio(
                near_atm_pe_oi,
                near_atm_ce_oi,
            )
        )

        return {
            "major_resistance_strike": (
                major_resistance_strike
            ),
            "major_resistance_oi": (
                major_resistance_oi
            ),
            "major_support_strike": (
                major_support_strike
            ),
            "major_support_oi": (
                major_support_oi
            ),
            "total_current_ce_oi": (
                total_ce_oi
            ),
            "total_current_pe_oi": (
                total_pe_oi
            ),
            "current_oi_pcr": (
                current_oi_pcr
            ),
            "ce_oi_concentration_pct": (
                ce_concentration_pct
            ),
            "pe_oi_concentration_pct": (
                pe_concentration_pct
            ),
            "distance_to_support": (
                distance_to_support
            ),
            "distance_to_resistance": (
                distance_to_resistance
            ),
            "spot_range_position": (
                range_position
            ),
            "near_atm_ce_oi": (
                near_atm_ce_oi
            ),
            "near_atm_pe_oi": (
                near_atm_pe_oi
            ),
            "near_atm_pcr": (
                near_atm_pcr
            ),
        }

    # --------------------------------------------------------
    # WHOLE OPTION-CHAIN INTELLIGENCE
    # --------------------------------------------------------

    def analyze_chain(
        self,
        comparisons,
        current_options=None,
        spot=None,
        atm=None,
    ):
        """
        Aggregate T1 -> T2 option activity into whole-chain
        OI intelligence.

        Important noise rule:
        NEUTRAL contracts remain included in diagnostics and
        total OI calculations, but are excluded from strongest
        actionable addition/unwinding identification.
        """

        if not comparisons:
            raise ValueError(
                "No OI comparisons provided"
            )

        # ----------------------------------------------------
        # CONTRACT COUNTS
        # ----------------------------------------------------

        long_buildup = 0
        short_buildup = 0
        long_unwinding = 0
        short_covering = 0
        neutral = 0

        # ----------------------------------------------------
        # ABSOLUTE OI WEIGHTS
        # ----------------------------------------------------

        resistance_strengthening_oi = 0.0
        resistance_weakening_oi = 0.0

        support_strengthening_oi = 0.0
        support_weakening_oi = 0.0

        total_ce_oi_change = 0.0
        total_pe_oi_change = 0.0

        total_ce_oi_change_pct = 0.0
        total_pe_oi_change_pct = 0.0

        # ----------------------------------------------------
        # CONTRACT COUNTERS
        # ----------------------------------------------------

        ce_resistance_strengthening = 0
        ce_resistance_weakening = 0

        pe_support_strengthening = 0
        pe_support_weakening = 0

        # ----------------------------------------------------
        # STRIKE-LEVEL CHANGE INTELLIGENCE
        # ----------------------------------------------------

        strongest_ce_addition = None
        strongest_pe_addition = None

        strongest_ce_unwinding = None
        strongest_pe_unwinding = None

        # ----------------------------------------------------
        # ANALYZE CONTRACTS
        # ----------------------------------------------------

        for row in comparisons:

            option_type = str(
                row.get(
                    "option_type",
                    "",
                )
            ).upper()

            classification = str(
                row.get(
                    "classification",
                    "NEUTRAL",
                )
            ).upper()

            interpretation = str(
                row.get(
                    "interpretation",
                    "DIRECTIONAL_ACTIVITY",
                )
            ).upper()

            oi_change = float(
                row.get(
                    "oi_change",
                    0,
                ) or 0
            )

            oi_change_pct = float(
                row.get(
                    "oi_change_pct",
                    0,
                ) or 0
            )

            try:
                strike = float(
                    row.get(
                        "strike",
                        0,
                    )
                )
            except (
                TypeError,
                ValueError,
            ):
                strike = 0.0

            oi_weight = abs(
                oi_change
            )

            # ------------------------------------------------
            # CLASSIFICATION COUNTS
            # ------------------------------------------------

            if classification == "LONG_BUILDUP":
                long_buildup += 1

            elif classification == "SHORT_BUILDUP":
                short_buildup += 1

            elif classification == "LONG_UNWINDING":
                long_unwinding += 1

            elif classification == "SHORT_COVERING":
                short_covering += 1

            else:
                neutral += 1

            # ------------------------------------------------
            # CE ANALYSIS
            # ------------------------------------------------

            if option_type == "CE":

                # All contracts, including NEUTRAL, remain
                # part of total delta-OI diagnostics.
                total_ce_oi_change += (
                    oi_change
                )

                total_ce_oi_change_pct += (
                    oi_change_pct
                )

                # IMPORTANT:
                # NEUTRAL contracts must NOT become the
                # strongest actionable CE addition.
                if (
                    oi_change > 0
                    and classification != "NEUTRAL"
                ):

                    candidate = {
                        "strike": strike,
                        "oi_change": (
                            oi_change
                        ),
                        "classification": (
                            classification
                        ),
                    }

                    if (
                        strongest_ce_addition
                        is None
                        or oi_change
                        > strongest_ce_addition[
                            "oi_change"
                        ]
                    ):
                        strongest_ce_addition = (
                            candidate
                        )

                # IMPORTANT:
                # NEUTRAL contracts must NOT become the
                # strongest actionable CE unwinding.
                elif (
                    oi_change < 0
                    and classification != "NEUTRAL"
                ):

                    candidate = {
                        "strike": strike,
                        "oi_change": (
                            oi_change
                        ),
                        "classification": (
                            classification
                        ),
                    }

                    if (
                        strongest_ce_unwinding
                        is None
                        or abs(
                            oi_change
                        )
                        > abs(
                            strongest_ce_unwinding[
                                "oi_change"
                            ]
                        )
                    ):
                        strongest_ce_unwinding = (
                            candidate
                        )

                if (
                    interpretation
                    == "RESISTANCE_STRENGTHENING"
                ):
                    ce_resistance_strengthening += 1

                    resistance_strengthening_oi += (
                        oi_weight
                    )

                elif (
                    interpretation
                    == "RESISTANCE_WEAKENING"
                ):
                    ce_resistance_weakening += 1

                    resistance_weakening_oi += (
                        oi_weight
                    )

            # ------------------------------------------------
            # PE ANALYSIS
            # ------------------------------------------------

            elif option_type == "PE":

                # All contracts, including NEUTRAL, remain
                # part of total delta-OI diagnostics.
                total_pe_oi_change += (
                    oi_change
                )

                total_pe_oi_change_pct += (
                    oi_change_pct
                )

                # IMPORTANT:
                # NEUTRAL contracts must NOT become the
                # strongest actionable PE addition.
                if (
                    oi_change > 0
                    and classification != "NEUTRAL"
                ):

                    candidate = {
                        "strike": strike,
                        "oi_change": (
                            oi_change
                        ),
                        "classification": (
                            classification
                        ),
                    }

                    if (
                        strongest_pe_addition
                        is None
                        or oi_change
                        > strongest_pe_addition[
                            "oi_change"
                        ]
                    ):
                        strongest_pe_addition = (
                            candidate
                        )

                # IMPORTANT:
                # NEUTRAL contracts must NOT become the
                # strongest actionable PE unwinding.
                elif (
                    oi_change < 0
                    and classification != "NEUTRAL"
                ):

                    candidate = {
                        "strike": strike,
                        "oi_change": (
                            oi_change
                        ),
                        "classification": (
                            classification
                        ),
                    }

                    if (
                        strongest_pe_unwinding
                        is None
                        or abs(
                            oi_change
                        )
                        > abs(
                            strongest_pe_unwinding[
                                "oi_change"
                            ]
                        )
                    ):
                        strongest_pe_unwinding = (
                            candidate
                        )

                if (
                    interpretation
                    == "SUPPORT_STRENGTHENING"
                ):
                    pe_support_strengthening += 1

                    support_strengthening_oi += (
                        oi_weight
                    )

                elif (
                    interpretation
                    == "SUPPORT_WEAKENING"
                ):
                    pe_support_weakening += 1

                    support_weakening_oi += (
                        oi_weight
                    )

        # ----------------------------------------------------
        # RESISTANCE STATE
        # ----------------------------------------------------

        if (
            resistance_strengthening_oi
            > resistance_weakening_oi
        ):
            resistance_state = (
                "STRENGTHENING"
            )

        elif (
            resistance_weakening_oi
            > resistance_strengthening_oi
        ):
            resistance_state = (
                "WEAKENING"
            )

        else:
            resistance_state = (
                "NEUTRAL"
            )

        # ----------------------------------------------------
        # SUPPORT STATE
        # ----------------------------------------------------

        if (
            support_strengthening_oi
            > support_weakening_oi
        ):
            support_state = (
                "STRENGTHENING"
            )

        elif (
            support_weakening_oi
            > support_strengthening_oi
        ):
            support_state = (
                "WEAKENING"
            )

        else:
            support_state = (
                "NEUTRAL"
            )

        # ----------------------------------------------------
        # CHAIN STRUCTURE
        # ----------------------------------------------------

        if (
            resistance_state == "STRENGTHENING"
            and support_state == "STRENGTHENING"
        ):
            chain_structure = (
                "RANGE_BUILDING"
            )

        elif (
            resistance_state == "WEAKENING"
            and support_state == "STRENGTHENING"
        ):
            chain_structure = (
                "BULLISH_SHIFT"
            )

        elif (
            resistance_state == "STRENGTHENING"
            and support_state == "WEAKENING"
        ):
            chain_structure = (
                "BEARISH_SHIFT"
            )

        elif (
            resistance_state == "WEAKENING"
            and support_state == "WEAKENING"
        ):
            chain_structure = (
                "VOLATILE_UNWINDING"
            )

        else:
            chain_structure = (
                "MIXED"
            )

        # ----------------------------------------------------
        # DELTA-OI PRESSURE
        # ----------------------------------------------------

        ce_positive_change = max(
            total_ce_oi_change,
            0.0,
        )

        pe_positive_change = max(
            total_pe_oi_change,
            0.0,
        )

        delta_oi_pcr = (
            self._safe_ratio(
                pe_positive_change,
                ce_positive_change,
            )
        )

        # ----------------------------------------------------
        # CURRENT ABSOLUTE OI STRUCTURE
        # ----------------------------------------------------

        current_structure = (
            self.analyze_current_oi(
                current_options=current_options,
                spot=spot,
                atm=atm,
            )
            if current_options
            else None
        )

        # ----------------------------------------------------
        # OI DIRECTIONAL BIAS
        # ----------------------------------------------------

        bullish_evidence = 0
        bearish_evidence = 0
        range_evidence = 0

        if chain_structure == "BULLISH_SHIFT":
            bullish_evidence += 2

        elif chain_structure == "BEARISH_SHIFT":
            bearish_evidence += 2

        elif chain_structure == "RANGE_BUILDING":
            range_evidence += 2

        if resistance_state == "WEAKENING":
            bullish_evidence += 1

        elif resistance_state == "STRENGTHENING":
            bearish_evidence += 1

        if support_state == "STRENGTHENING":
            bullish_evidence += 1

        elif support_state == "WEAKENING":
            bearish_evidence += 1

        if (
            total_pe_oi_change > 0
            and total_ce_oi_change < 0
        ):
            bullish_evidence += 2

        elif (
            total_ce_oi_change > 0
            and total_pe_oi_change < 0
        ):
            bearish_evidence += 2

        if current_structure:

            current_pcr = (
                current_structure.get(
                    "current_oi_pcr"
                )
            )

            near_atm_pcr = (
                current_structure.get(
                    "near_atm_pcr"
                )
            )

            if current_pcr is not None:

                if current_pcr >= 1.10:
                    bullish_evidence += 1

                elif current_pcr <= 0.90:
                    bearish_evidence += 1

                else:
                    range_evidence += 1

            if near_atm_pcr is not None:

                if near_atm_pcr >= 1.10:
                    bullish_evidence += 1

                elif near_atm_pcr <= 0.90:
                    bearish_evidence += 1

                else:
                    range_evidence += 1

        # ----------------------------------------------------
        # FINAL BIAS
        # ----------------------------------------------------

        highest_score = max(
            bullish_evidence,
            bearish_evidence,
            range_evidence,
        )

        winners = sum([
            bullish_evidence
            == highest_score,

            bearish_evidence
            == highest_score,

            range_evidence
            == highest_score,
        ])

        if (
            highest_score == 0
            or winners > 1
        ):
            oi_directional_bias = (
                "MIXED"
            )

        elif (
            bullish_evidence
            == highest_score
        ):
            oi_directional_bias = (
                "BULLISH"
            )

        elif (
            bearish_evidence
            == highest_score
        ):
            oi_directional_bias = (
                "BEARISH"
            )

        else:
            oi_directional_bias = (
                "RANGE"
            )

        # ----------------------------------------------------
        # EVIDENCE-AGREEMENT CONFIDENCE
        # ----------------------------------------------------

        scores = sorted(
            [
                bullish_evidence,
                bearish_evidence,
                range_evidence,
            ],
            reverse=True,
        )

        top_score = scores[0]
        second_score = scores[1]

        score_gap = (
            top_score
            - second_score
        )

        if (
            oi_directional_bias == "MIXED"
        ):
            oi_confidence = "LOW"

        elif (
            top_score >= 6
            and score_gap >= 3
        ):
            oi_confidence = "HIGH"

        elif (
            top_score >= 4
            and score_gap >= 2
        ):
            oi_confidence = "MEDIUM"

        else:
            oi_confidence = "LOW"

        # ----------------------------------------------------
        # RETURN ANALYSIS
        # ----------------------------------------------------

        result = {
            "contracts_analyzed": len(
                comparisons
            ),

            "resistance_state": (
                resistance_state
            ),

            "support_state": (
                support_state
            ),

            "chain_structure": (
                chain_structure
            ),

            "oi_directional_bias": (
                oi_directional_bias
            ),

            "oi_confidence": (
                oi_confidence
            ),

            "bullish_evidence_score": (
                bullish_evidence
            ),

            "bearish_evidence_score": (
                bearish_evidence
            ),

            "range_evidence_score": (
                range_evidence
            ),

            # Contract counts
            "ce_resistance_strengthening": (
                ce_resistance_strengthening
            ),

            "ce_resistance_weakening": (
                ce_resistance_weakening
            ),

            "pe_support_strengthening": (
                pe_support_strengthening
            ),

            "pe_support_weakening": (
                pe_support_weakening
            ),

            # OI weighted strength
            "resistance_strengthening_oi": (
                resistance_strengthening_oi
            ),

            "resistance_weakening_oi": (
                resistance_weakening_oi
            ),

            "support_strengthening_oi": (
                support_strengthening_oi
            ),

            "support_weakening_oi": (
                support_weakening_oi
            ),

            # Net delta OI
            "total_ce_oi_change": (
                total_ce_oi_change
            ),

            "total_pe_oi_change": (
                total_pe_oi_change
            ),

            "delta_oi_pcr": (
                delta_oi_pcr
            ),

            # Diagnostics
            "total_ce_oi_change_pct": (
                total_ce_oi_change_pct
            ),

            "total_pe_oi_change_pct": (
                total_pe_oi_change_pct
            ),

            # Strike-level fresh actionable activity
            "strongest_ce_addition": (
                strongest_ce_addition
            ),

            "strongest_pe_addition": (
                strongest_pe_addition
            ),

            "strongest_ce_unwinding": (
                strongest_ce_unwinding
            ),

            "strongest_pe_unwinding": (
                strongest_pe_unwinding
            ),

            # Activity counts
            "long_buildup_count": (
                long_buildup
            ),

            "short_buildup_count": (
                short_buildup
            ),

            "long_unwinding_count": (
                long_unwinding
            ),

            "short_covering_count": (
                short_covering
            ),

            "neutral_count": (
                neutral
            ),
        }

        # ----------------------------------------------------
        # MERGE CURRENT OI STRUCTURE
        # ----------------------------------------------------

        if current_structure:

            result.update(
                current_structure
            )

        else:

            result.update({
                "major_resistance_strike": None,
                "major_resistance_oi": None,

                "major_support_strike": None,
                "major_support_oi": None,

                "total_current_ce_oi": None,
                "total_current_pe_oi": None,

                "current_oi_pcr": None,

                "ce_oi_concentration_pct": None,
                "pe_oi_concentration_pct": None,

                "distance_to_support": None,
                "distance_to_resistance": None,

                "spot_range_position": (
                    "UNKNOWN"
                ),

                "near_atm_ce_oi": None,
                "near_atm_pe_oi": None,
                "near_atm_pcr": None,
            })

        return result