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
            "previous_price": float(previous_price),
            "current_price": float(current_price),
            "previous_oi": float(previous_oi),
            "current_oi": float(current_oi),
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

        Other classifications are retained as directional
        option activity without forcing a support/resistance
        conclusion.
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