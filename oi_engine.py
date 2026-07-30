# ============================================================
# THETA AI TRADER — OPEN INTEREST INTELLIGENCE ENGINE
# ============================================================


class OIEngine:
    """
    Analyzes price and Open Interest behaviour.

    Core classifications:
    - LONG_BUILDUP
    - SHORT_BUILDUP
    - LONG_UNWINDING
    - SHORT_COVERING
    - NEUTRAL

    This engine performs analysis only.

    It does NOT:
    - Fetch broker data
    - Select option strikes
    - Generate orders
    - Place orders
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
        Classify market participation using price and OI.

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

        CE short buildup:
            Resistance strengthening.

        CE short covering:
            Resistance weakening.

        PE short buildup:
            Support strengthening.

        PE short covering:
            Support weakening.
        """

        option_type = str(
            option_type
        ).upper()

        classification = str(
            classification
        ).upper()

        if option_type not in ("CE", "PE"):
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
    # WHOLE OPTION-CHAIN INTELLIGENCE
    # --------------------------------------------------------

    def analyze_chain(
        self,
        comparisons,
    ):
        """
        Aggregate individual option-contract activity into
        whole-chain OI intelligence.

        Uses absolute OI change as the primary weighting
        mechanism for support/resistance strength.

        comparisons should contain results produced by
        OptionSnapshotEngine.compare_snapshots().
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

        # Keep percentage totals for diagnostics only.
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
        # ANALYZE CONTRACTS
        # ----------------------------------------------------

        for row in comparisons:

            option_type = str(
                row.get(
                    "option_type",
                    ""
                )
            ).upper()

            classification = str(
                row.get(
                    "classification",
                    "NEUTRAL"
                )
            ).upper()

            interpretation = str(
                row.get(
                    "interpretation",
                    "DIRECTIONAL_ACTIVITY"
                )
            ).upper()

            oi_change = float(
                row.get(
                    "oi_change",
                    0
                ) or 0
            )

            oi_change_pct = float(
                row.get(
                    "oi_change_pct",
                    0
                ) or 0
            )

            # Absolute magnitude is used for weighting.
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

                total_ce_oi_change += (
                    oi_change
                )

                total_ce_oi_change_pct += (
                    oi_change_pct
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

                total_pe_oi_change += (
                    oi_change
                )

                total_pe_oi_change_pct += (
                    oi_change_pct
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
        # RESISTANCE STATE — OI WEIGHTED
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
        # SUPPORT STATE — OI WEIGHTED
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
        # OPTION-CHAIN STRUCTURE
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
        # RETURN ANALYSIS
        # ----------------------------------------------------

        return {
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

            # OI-weighted strength
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

            # Net absolute OI changes
            "total_ce_oi_change": (
                total_ce_oi_change
            ),

            "total_pe_oi_change": (
                total_pe_oi_change
            ),

            # Diagnostic percentage totals
            "total_ce_oi_change_pct": (
                total_ce_oi_change_pct
            ),

            "total_pe_oi_change_pct": (
                total_pe_oi_change_pct
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