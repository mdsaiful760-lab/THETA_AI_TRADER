# ============================================================
# THETA AI TRADER — OPTION MARKET INTELLIGENCE ENGINE
# ============================================================

import math
from copy import deepcopy


class OptionMarketIntelligenceEngine:
    """
    Analytical option-chain intelligence engine.

    INPUT
    -----
    Enriched option contracts produced by OptionGreeksEngine.

    RESPONSIBILITIES
    ----------------
    - Validate enriched option-chain data
    - Detect ATM strike
    - Calculate ATM straddle
    - Calculate straddle-implied expected move
    - Calculate CE / PE open-interest structure
    - Calculate OI PCR
    - Detect call / put OI walls
    - Measure OI concentration
    - Calculate ATM IV structure
    - Measure CE / PE IV skew
    - Measure wing IV / smile structure
    - Analyse gamma concentration
    - Analyse theta / vega structure
    - Produce analytical support / resistance candidates
    - Produce data-quality diagnostics

    IMPORTANT
    ---------
    This engine is ANALYTICAL ONLY.

    It has:
        NO contract-selection authority
        NO trade-decision authority
        NO strategy-selection authority
        NO risk-allocation authority
        NO position-sizing authority
        NO broker/order authority

    Nothing returned by this engine is an instruction to trade.
    """

    VALID_OPTION_TYPES = {"CE", "PE"}

    # ========================================================
    # INITIALIZATION
    # ========================================================

    def __init__(
        self,
        minimum_complete_pairs=3,
        wall_count=3,
        wing_distance_steps=3,
    ):
        self.minimum_complete_pairs = max(
            1,
            int(minimum_complete_pairs),
        )

        self.wall_count = max(
            1,
            int(wall_count),
        )

        self.wing_distance_steps = max(
            1,
            int(wing_distance_steps),
        )

    # ========================================================
    # SAFE HELPERS
    # ========================================================

    @staticmethod
    def _safe_float(value, default=None):
        try:
            if value is None:
                return default

            result = float(value)

            if not math.isfinite(result):
                return default

            return result

        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_non_negative(value, default=None):
        value = (
            OptionMarketIntelligenceEngine
            ._safe_float(
                value,
                default=None,
            )
        )

        if value is None:
            return default

        if value < 0:
            return default

        return value

    @staticmethod
    def _normalize_option_type(value):
        if value is None:
            return ""

        return str(value).strip().upper()

    @staticmethod
    def _mean(values):
        clean = [
            float(value)
            for value in values
            if value is not None
            and math.isfinite(float(value))
        ]

        if not clean:
            return None

        return sum(clean) / len(clean)

    @staticmethod
    def _median(values):
        clean = sorted(
            float(value)
            for value in values
            if value is not None
            and math.isfinite(float(value))
        )

        if not clean:
            return None

        length = len(clean)
        middle = length // 2

        if length % 2 == 1:
            return clean[middle]

        return (
            clean[middle - 1]
            + clean[middle]
        ) / 2.0

    @staticmethod
    def _percentage(
        numerator,
        denominator,
    ):
        numerator = (
            OptionMarketIntelligenceEngine
            ._safe_float(numerator)
        )

        denominator = (
            OptionMarketIntelligenceEngine
            ._safe_float(denominator)
        )

        if (
            numerator is None
            or denominator is None
            or denominator == 0
        ):
            return None

        return (
            numerator
            / denominator
            * 100.0
        )

    # ========================================================
    # AUTHORITY INVARIANTS
    # ========================================================

    @staticmethod
    def _authority_flags():
        """
        Explicit authority denial.

        These values must never become True inside this engine.
        """

        return {
            "contract_selection_allowed": False,
            "trade_decision_allowed": False,
            "strategy_selection_allowed": False,
            "risk_allocation_allowed": False,
            "position_sizing_allowed": False,
            "broker_order_allowed": False,
        }

    # ========================================================
    # BLOCKED RESULT
    # ========================================================

    def _blocked_result(
        self,
        reason,
        errors=None,
        warnings=None,
        input_count=0,
    ):
        if errors is None:
            errors = []

        if warnings is None:
            warnings = []

        result = {
            "intelligence_permission": "BLOCK",
            "intelligence_allowed": False,
            "reason": str(reason),

            "input_count": int(input_count),
            "valid_contract_count": 0,
            "complete_pair_count": 0,

            "errors": list(errors),
            "warnings": list(warnings),

            "analytics": None,

            "data_quality": {
                "status": "BLOCKED",
                "score": 0.0,
            },
        }

        result.update(
            self._authority_flags()
        )

        return result

    # ========================================================
    # CONTRACT VALIDATION
    # ========================================================

    def _normalize_contract(
        self,
        contract,
    ):
        """
        Returns a sanitized COPY of one enriched contract.

        Original input is never modified.
        """

        if not isinstance(
            contract,
            dict,
        ):
            return None, [
                "CONTRACT_MUST_BE_DICTIONARY"
            ]

        required_fields = [
            "tradingsymbol",
            "strike",
            "option_type",
            "iv",
            "delta",
            "gamma",
            "theta",
            "vega",
        ]

        missing = [
            field
            for field in required_fields
            if contract.get(field) is None
        ]

        if missing:
            return None, [
                f"MISSING_{field.upper()}"
                for field in missing
            ]

        strike = self._safe_float(
            contract.get("strike")
        )

        option_type = (
            self._normalize_option_type(
                contract.get(
                    "option_type"
                )
            )
        )

        iv = self._safe_non_negative(
            contract.get("iv")
        )

        delta = self._safe_float(
            contract.get("delta")
        )

        gamma = self._safe_non_negative(
            contract.get("gamma")
        )

        theta = self._safe_float(
            contract.get("theta")
        )

        vega = self._safe_non_negative(
            contract.get("vega")
        )

        if (
            strike is None
            or strike <= 0
        ):
            return None, [
                "INVALID_STRIKE"
            ]

        if (
            option_type
            not in self.VALID_OPTION_TYPES
        ):
            return None, [
                "INVALID_OPTION_TYPE"
            ]

        if (
            iv is None
            or iv <= 0
        ):
            return None, [
                "INVALID_IV"
            ]

        if delta is None:
            return None, [
                "INVALID_DELTA"
            ]

        if gamma is None:
            return None, [
                "INVALID_GAMMA"
            ]

        if theta is None:
            return None, [
                "INVALID_THETA"
            ]

        if vega is None:
            return None, [
                "INVALID_VEGA"
            ]

        normalized = deepcopy(
            contract
        )

        normalized["strike"] = strike
        normalized["option_type"] = option_type
        normalized["iv"] = iv
        normalized["delta"] = delta
        normalized["gamma"] = gamma
        normalized["theta"] = theta
        normalized["vega"] = vega

        normalized["oi"] = (
            self._safe_non_negative(
                contract.get("oi"),
                default=0.0,
            )
        )

        normalized["ltp"] = (
            self._safe_non_negative(
                contract.get("ltp")
            )
        )

        normalized["bid"] = (
            self._safe_non_negative(
                contract.get("bid")
            )
        )

        normalized["ask"] = (
            self._safe_non_negative(
                contract.get("ask")
            )
        )

        normalized[
            "greeks_market_price"
        ] = self._safe_non_negative(
            contract.get(
                "greeks_market_price"
            )
        )

        normalized[
            "implied_forward"
        ] = self._safe_float(
            contract.get(
                "implied_forward"
            )
        )

        normalized[
            "time_to_expiry_years"
        ] = self._safe_non_negative(
            contract.get(
                "time_to_expiry_years"
            )
        )

        normalized[
            "pricing_mode"
        ] = str(
            contract.get(
                "pricing_mode",
                "",
            )
        ).strip().upper()

        normalized[
            "delta_basis"
        ] = str(
            contract.get(
                "delta_basis",
                "",
            )
        ).strip().upper()

        normalized[
            "gamma_basis"
        ] = str(
            contract.get(
                "gamma_basis",
                "",
            )
        ).strip().upper()

        return normalized, []

    # ========================================================
    # BUILD STRIKE MAP
    # ========================================================

    def _build_strike_map(
        self,
        contracts,
    ):
        strike_map = {}

        for contract in contracts:

            strike = contract["strike"]
            option_type = (
                contract["option_type"]
            )

            if strike not in strike_map:
                strike_map[strike] = {}

            # Defensive duplicate handling:
            # retain the contract with greater OI.
            existing = (
                strike_map[strike]
                .get(option_type)
            )

            if existing is None:
                strike_map[strike][
                    option_type
                ] = contract

            else:
                existing_oi = (
                    self._safe_non_negative(
                        existing.get("oi"),
                        default=0.0,
                    )
                )

                new_oi = (
                    self._safe_non_negative(
                        contract.get("oi"),
                        default=0.0,
                    )
                )

                if new_oi > existing_oi:
                    strike_map[strike][
                        option_type
                    ] = contract

        return strike_map

    # ========================================================
    # COMPLETE PAIRS
    # ========================================================

    @staticmethod
    def _complete_pair_strikes(
        strike_map,
    ):
        return sorted(
            strike
            for strike, legs
            in strike_map.items()
            if (
                "CE" in legs
                and "PE" in legs
            )
        )

    # ========================================================
    # STRIKE STEP
    # ========================================================

    def _detect_strike_step(
        self,
        strikes,
    ):
        strikes = sorted(
            set(
                self._safe_float(strike)
                for strike in strikes
                if self._safe_float(strike)
                is not None
            )
        )

        if len(strikes) < 2:
            return None

        differences = []

        for index in range(
            len(strikes) - 1
        ):
            difference = (
                strikes[index + 1]
                - strikes[index]
            )

            if difference > 0:
                differences.append(
                    difference
                )

        if not differences:
            return None

        # Median is safer than minimum when malformed
        # or unusually dense strikes are present.
        return self._median(
            differences
        )

    # ========================================================
    # REFERENCE PRICE
    # ========================================================

    def _resolve_reference_price(
        self,
        contracts,
        spot_price=None,
        implied_forward=None,
    ):
        """
        FORWARD pricing should primarily use implied forward
        for ATM/moneyness analytics.

        SPOT remains acceptable as an explicit reference when
        forward is unavailable.
        """

        explicit_forward = (
            self._safe_float(
                implied_forward
            )
        )

        if (
            explicit_forward is not None
            and explicit_forward > 0
        ):
            return (
                explicit_forward,
                "IMPLIED_FORWARD",
            )

        contract_forwards = [
            self._safe_float(
                contract.get(
                    "implied_forward"
                )
            )
            for contract in contracts
        ]

        contract_forwards = [
            value
            for value in contract_forwards
            if (
                value is not None
                and value > 0
            )
        ]

        if contract_forwards:
            return (
                self._median(
                    contract_forwards
                ),
                "IMPLIED_FORWARD",
            )

        spot = self._safe_float(
            spot_price
        )

        if (
            spot is not None
            and spot > 0
        ):
            return (
                spot,
                "SPOT",
            )

        return None, None

    # ========================================================
    # ATM DETECTION
    # ========================================================

    @staticmethod
    def _find_atm_strike(
        strikes,
        reference_price,
    ):
        if (
            not strikes
            or reference_price is None
        ):
            return None

        return min(
            strikes,
            key=lambda strike: (
                abs(
                    strike
                    - reference_price
                ),
                strike,
            ),
        )

    # ========================================================
    # OPTION PRICE
    # ========================================================

    def _analytics_price(
        self,
        contract,
    ):
        """
        Prefer the same market price already chosen by
        OptionGreeksEngine.

        Fall back to valid bid/ask midpoint, then LTP.
        """

        greeks_price = (
            self._safe_non_negative(
                contract.get(
                    "greeks_market_price"
                )
            )
        )

        if (
            greeks_price is not None
            and greeks_price > 0
        ):
            return (
                greeks_price,
                str(
                    contract.get(
                        "greeks_price_source",
                        "GREEKS_MARKET_PRICE",
                    )
                ),
            )

        bid = self._safe_non_negative(
            contract.get("bid")
        )

        ask = self._safe_non_negative(
            contract.get("ask")
        )

        if (
            bid is not None
            and ask is not None
            and bid > 0
            and ask > 0
            and ask >= bid
        ):
            return (
                (bid + ask) / 2.0,
                "MIDPOINT",
            )

        ltp = self._safe_non_negative(
            contract.get("ltp")
        )

        if (
            ltp is not None
            and ltp > 0
        ):
            return ltp, "LTP"

        return None, None

    # ========================================================
    # ATM STRADDLE
    # ========================================================

    def _calculate_atm_straddle(
        self,
        strike_map,
        atm_strike,
        reference_price,
    ):
        legs = strike_map.get(
            atm_strike,
            {},
        )

        ce = legs.get("CE")
        pe = legs.get("PE")

        if ce is None or pe is None:
            return {
                "available": False,
                "reason": "ATM_PAIR_INCOMPLETE",
            }

        ce_price, ce_source = (
            self._analytics_price(
                ce
            )
        )

        pe_price, pe_source = (
            self._analytics_price(
                pe
            )
        )

        if (
            ce_price is None
            or pe_price is None
        ):
            return {
                "available": False,
                "reason": "ATM_MARKET_PRICE_UNAVAILABLE",
            }

        straddle = (
            ce_price + pe_price
        )

        expected_move_points = (
            straddle
        )

        expected_move_percent = (
            self._percentage(
                expected_move_points,
                reference_price,
            )
        )

        lower_bound = (
            reference_price
            - expected_move_points
        )

        upper_bound = (
            reference_price
            + expected_move_points
        )

        return {
            "available": True,
            "atm_strike": atm_strike,

            "ce_price": ce_price,
            "pe_price": pe_price,

            "ce_price_source": ce_source,
            "pe_price_source": pe_source,

            "straddle_price": straddle,

            # This is a simple premium-implied range proxy,
            # not a statistical probability interval.
            "expected_move_method":
                "ATM_STRADDLE_PREMIUM_PROXY",

            "expected_move_points":
                expected_move_points,

            "expected_move_percent":
                expected_move_percent,

            "lower_implied_range":
                lower_bound,

            "upper_implied_range":
                upper_bound,
        }

    # ========================================================
    # OI STRUCTURE
    # ========================================================

    def _calculate_oi_structure(
        self,
        contracts,
    ):
        ce_contracts = [
            contract
            for contract in contracts
            if (
                contract[
                    "option_type"
                ] == "CE"
            )
        ]

        pe_contracts = [
            contract
            for contract in contracts
            if (
                contract[
                    "option_type"
                ] == "PE"
            )
        ]

        total_ce_oi = sum(
            self._safe_non_negative(
                contract.get("oi"),
                default=0.0,
            )
            for contract
            in ce_contracts
        )

        total_pe_oi = sum(
            self._safe_non_negative(
                contract.get("oi"),
                default=0.0,
            )
            for contract
            in pe_contracts
        )

        if total_ce_oi > 0:
            oi_pcr = (
                total_pe_oi
                / total_ce_oi
            )
        else:
            oi_pcr = None

        ce_ranked = sorted(
            ce_contracts,
            key=lambda contract: (
                self._safe_non_negative(
                    contract.get("oi"),
                    default=0.0,
                ),
                -contract["strike"],
            ),
            reverse=True,
        )

        pe_ranked = sorted(
            pe_contracts,
            key=lambda contract: (
                self._safe_non_negative(
                    contract.get("oi"),
                    default=0.0,
                ),
                contract["strike"],
            ),
            reverse=True,
        )

        call_walls = []

        for contract in (
            ce_ranked[
                :self.wall_count
            ]
        ):
            oi = (
                self._safe_non_negative(
                    contract.get("oi"),
                    default=0.0,
                )
            )

            call_walls.append(
                {
                    "strike":
                        contract["strike"],

                    "oi":
                        oi,

                    "share_of_ce_oi_percent":
                        self._percentage(
                            oi,
                            total_ce_oi,
                        ),

                    "tradingsymbol":
                        contract.get(
                            "tradingsymbol"
                        ),
                }
            )

        put_walls = []

        for contract in (
            pe_ranked[
                :self.wall_count
            ]
        ):
            oi = (
                self._safe_non_negative(
                    contract.get("oi"),
                    default=0.0,
                )
            )

            put_walls.append(
                {
                    "strike":
                        contract["strike"],

                    "oi":
                        oi,

                    "share_of_pe_oi_percent":
                        self._percentage(
                            oi,
                            total_pe_oi,
                        ),

                    "tradingsymbol":
                        contract.get(
                            "tradingsymbol"
                        ),
                }
            )

        highest_call_wall = (
            call_walls[0]
            if call_walls
            else None
        )

        highest_put_wall = (
            put_walls[0]
            if put_walls
            else None
        )

        top_call_oi = (
            highest_call_wall["oi"]
            if highest_call_wall
            else 0.0
        )

        top_put_oi = (
            highest_put_wall["oi"]
            if highest_put_wall
            else 0.0
        )

        return {
            "total_ce_oi":
                total_ce_oi,

            "total_pe_oi":
                total_pe_oi,

            "total_chain_oi":
                total_ce_oi
                + total_pe_oi,

            "oi_pcr":
                oi_pcr,

            "call_walls":
                call_walls,

            "put_walls":
                put_walls,

            "highest_call_wall":
                highest_call_wall,

            "highest_put_wall":
                highest_put_wall,

            "top_call_oi_concentration_percent":
                self._percentage(
                    top_call_oi,
                    total_ce_oi,
                ),

            "top_put_oi_concentration_percent":
                self._percentage(
                    top_put_oi,
                    total_pe_oi,
                ),
        }

    # ========================================================
    # ATM IV STRUCTURE
    # ========================================================

    def _calculate_iv_structure(
        self,
        strike_map,
        complete_pair_strikes,
        atm_strike,
        strike_step,
    ):
        atm_legs = strike_map.get(
            atm_strike,
            {},
        )

        atm_ce = atm_legs.get("CE")
        atm_pe = atm_legs.get("PE")

        atm_ce_iv = (
            self._safe_float(
                atm_ce.get("iv")
            )
            if atm_ce
            else None
        )

        atm_pe_iv = (
            self._safe_float(
                atm_pe.get("iv")
            )
            if atm_pe
            else None
        )

        atm_iv = self._mean(
            [
                atm_ce_iv,
                atm_pe_iv,
            ]
        )

        ce_minus_pe = None

        if (
            atm_ce_iv is not None
            and atm_pe_iv is not None
        ):
            ce_minus_pe = (
                atm_ce_iv
                - atm_pe_iv
            )

        pair_iv_curve = []

        for strike in (
            complete_pair_strikes
        ):
            legs = strike_map[
                strike
            ]

            ce_iv = self._safe_float(
                legs["CE"].get("iv")
            )

            pe_iv = self._safe_float(
                legs["PE"].get("iv")
            )

            average_iv = self._mean(
                [
                    ce_iv,
                    pe_iv,
                ]
            )

            pair_iv_curve.append(
                {
                    "strike": strike,
                    "ce_iv": ce_iv,
                    "pe_iv": pe_iv,
                    "average_iv":
                        average_iv,

                    "ce_minus_pe_iv":
                        (
                            ce_iv - pe_iv
                            if (
                                ce_iv
                                is not None
                                and pe_iv
                                is not None
                            )
                            else None
                        ),
                }
            )

        lower_wing = None
        upper_wing = None

        if strike_step is not None:
            lower_target = (
                atm_strike
                - (
                    self.wing_distance_steps
                    * strike_step
                )
            )

            upper_target = (
                atm_strike
                + (
                    self.wing_distance_steps
                    * strike_step
                )
            )

            lower_candidates = [
                item
                for item in pair_iv_curve
                if item["strike"]
                < atm_strike
            ]

            upper_candidates = [
                item
                for item in pair_iv_curve
                if item["strike"]
                > atm_strike
            ]

            if lower_candidates:
                lower_wing = min(
                    lower_candidates,
                    key=lambda item: abs(
                        item["strike"]
                        - lower_target
                    ),
                )

            if upper_candidates:
                upper_wing = min(
                    upper_candidates,
                    key=lambda item: abs(
                        item["strike"]
                        - upper_target
                    ),
                )

        lower_wing_iv = (
            lower_wing[
                "average_iv"
            ]
            if lower_wing
            else None
        )

        upper_wing_iv = (
            upper_wing[
                "average_iv"
            ]
            if upper_wing
            else None
        )

        lower_vs_atm = None
        upper_vs_atm = None
        wing_skew = None

        if (
            lower_wing_iv is not None
            and atm_iv is not None
        ):
            lower_vs_atm = (
                lower_wing_iv
                - atm_iv
            )

        if (
            upper_wing_iv is not None
            and atm_iv is not None
        ):
            upper_vs_atm = (
                upper_wing_iv
                - atm_iv
            )

        if (
            lower_wing_iv is not None
            and upper_wing_iv is not None
        ):
            wing_skew = (
                lower_wing_iv
                - upper_wing_iv
            )

        return {
            "atm_ce_iv":
                atm_ce_iv,

            "atm_pe_iv":
                atm_pe_iv,

            "atm_average_iv":
                atm_iv,

            "atm_ce_minus_pe_iv":
                ce_minus_pe,

            "iv_curve":
                pair_iv_curve,

            "lower_wing":
                lower_wing,

            "upper_wing":
                upper_wing,

            "lower_wing_minus_atm_iv":
                lower_vs_atm,

            "upper_wing_minus_atm_iv":
                upper_vs_atm,

            # Positive means lower-strike wing IV
            # exceeds upper-strike wing IV.
            "lower_minus_upper_wing_iv":
                wing_skew,
        }

    # ========================================================
    # GREEKS STRUCTURE
    # ========================================================

    def _calculate_greeks_structure(
        self,
        contracts,
        strike_map,
    ):
        gamma_by_strike = []

        for strike in sorted(
            strike_map.keys()
        ):
            legs = strike_map[
                strike
            ]

            ce_gamma = (
                self._safe_non_negative(
                    legs["CE"].get(
                        "gamma"
                    ),
                    default=0.0,
                )
                if "CE" in legs
                else 0.0
            )

            pe_gamma = (
                self._safe_non_negative(
                    legs["PE"].get(
                        "gamma"
                    ),
                    default=0.0,
                )
                if "PE" in legs
                else 0.0
            )

            combined_gamma = (
                ce_gamma
                + pe_gamma
            )

            gamma_by_strike.append(
                {
                    "strike":
                        strike,

                    "ce_gamma":
                        ce_gamma,

                    "pe_gamma":
                        pe_gamma,

                    "combined_gamma":
                        combined_gamma,
                }
            )

        highest_gamma_strike = None

        if gamma_by_strike:
            highest_gamma_strike = max(
                gamma_by_strike,
                key=lambda item: (
                    item[
                        "combined_gamma"
                    ],
                    -item["strike"],
                ),
            )

        total_abs_theta = sum(
            abs(
                self._safe_float(
                    contract.get(
                        "theta"
                    ),
                    default=0.0,
                )
            )
            for contract
            in contracts
        )

        total_vega = sum(
            self._safe_non_negative(
                contract.get(
                    "vega"
                ),
                default=0.0,
            )
            for contract
            in contracts
        )

        ce_theta = sum(
            abs(
                self._safe_float(
                    contract.get(
                        "theta"
                    ),
                    default=0.0,
                )
            )
            for contract
            in contracts
            if (
                contract[
                    "option_type"
                ] == "CE"
            )
        )

        pe_theta = sum(
            abs(
                self._safe_float(
                    contract.get(
                        "theta"
                    ),
                    default=0.0,
                )
            )
            for contract
            in contracts
            if (
                contract[
                    "option_type"
                ] == "PE"
            )
        )

        ce_vega = sum(
            self._safe_non_negative(
                contract.get(
                    "vega"
                ),
                default=0.0,
            )
            for contract
            in contracts
            if (
                contract[
                    "option_type"
                ] == "CE"
            )
        )

        pe_vega = sum(
            self._safe_non_negative(
                contract.get(
                    "vega"
                ),
                default=0.0,
            )
            for contract
            in contracts
            if (
                contract[
                    "option_type"
                ] == "PE"
            )
        )

        return {
            "gamma_by_strike":
                gamma_by_strike,

            "highest_combined_gamma_strike":
                highest_gamma_strike,

            "total_absolute_theta":
                total_abs_theta,

            "ce_absolute_theta":
                ce_theta,

            "pe_absolute_theta":
                pe_theta,

            "total_vega":
                total_vega,

            "ce_vega":
                ce_vega,

            "pe_vega":
                pe_vega,
        }

    # ========================================================
    # ANALYTICAL MARKET STRUCTURE
    # ========================================================

    @staticmethod
    def _calculate_market_structure(
        oi_structure,
    ):
        """
        OI-derived structural candidates only.

        These are NOT guaranteed support/resistance levels
        and are NOT trade signals.
        """

        highest_call = (
            oi_structure.get(
                "highest_call_wall"
            )
        )

        highest_put = (
            oi_structure.get(
                "highest_put_wall"
            )
        )

        resistance_candidate = (
            highest_call.get(
                "strike"
            )
            if highest_call
            else None
        )

        support_candidate = (
            highest_put.get(
                "strike"
            )
            if highest_put
            else None
        )

        return {
            "oi_resistance_candidate":
                resistance_candidate,

            "oi_support_candidate":
                support_candidate,

            "interpretation":
                "OI_CONCENTRATION_ONLY",

            "is_trade_signal":
                False,
        }

    # ========================================================
    # DATA QUALITY
    # ========================================================

    def _calculate_data_quality(
        self,
        input_count,
        valid_contract_count,
        complete_pair_count,
        contracts,
        atm_straddle,
    ):
        """
        Transparent heuristic quality score.

        It is a data-completeness diagnostic,
        not a market-confidence score.
        """

        score = 100.0
        warnings = []

        if input_count <= 0:
            return {
                "status": "BLOCKED",
                "score": 0.0,
                "warnings": [
                    "NO_INPUT_CONTRACTS"
                ],
            }

        valid_ratio = (
            valid_contract_count
            / input_count
        )

        if valid_ratio < 1.0:
            score -= (
                (1.0 - valid_ratio)
                * 40.0
            )

            warnings.append(
                "SOME_CONTRACTS_INVALID"
            )

        if (
            complete_pair_count
            < self.minimum_complete_pairs
        ):
            score -= 35.0

            warnings.append(
                "INSUFFICIENT_COMPLETE_PAIRS"
            )

        if not atm_straddle.get(
            "available",
            False,
        ):
            score -= 20.0

            warnings.append(
                "ATM_STRADDLE_UNAVAILABLE"
            )

        pricing_modes = {
            str(
                contract.get(
                    "pricing_mode",
                    ""
                )
            ).strip().upper()
            for contract
            in contracts
            if contract.get(
                "pricing_mode"
            )
        }

        if len(pricing_modes) > 1:
            score -= 20.0

            warnings.append(
                "MIXED_PRICING_MODES"
            )

        delta_bases = {
            str(
                contract.get(
                    "delta_basis",
                    ""
                )
            ).strip().upper()
            for contract
            in contracts
            if contract.get(
                "delta_basis"
            )
        }

        if len(delta_bases) > 1:
            score -= 15.0

            warnings.append(
                "MIXED_DELTA_BASES"
            )

        gamma_bases = {
            str(
                contract.get(
                    "gamma_basis",
                    ""
                )
            ).strip().upper()
            for contract
            in contracts
            if contract.get(
                "gamma_basis"
            )
        }

        if len(gamma_bases) > 1:
            score -= 15.0

            warnings.append(
                "MIXED_GAMMA_BASES"
            )

        score = max(
            0.0,
            min(
                100.0,
                score,
            ),
        )

        if score >= 90.0:
            status = "HIGH"

        elif score >= 75.0:
            status = "GOOD"

        elif score >= 50.0:
            status = "CAUTION"

        else:
            status = "LOW"

        return {
            "status": status,
            "score": score,
            "warnings": warnings,
        }

    # ========================================================
    # MAIN ANALYSIS METHOD
    # ========================================================

    def analyze_option_chain(
        self,
        contracts,
        spot_price=None,
        implied_forward=None,
    ):
        """
        Main public method.

        Returns analytical option-market intelligence only.
        """

        if contracts is None:
            return self._blocked_result(
                reason="CONTRACTS_REQUIRED",
                errors=[
                    "CONTRACT_COLLECTION_IS_NONE"
                ],
            )

        try:
            contract_list = list(
                contracts
            )

        except TypeError:
            return self._blocked_result(
                reason="INVALID_CONTRACT_COLLECTION",
                errors=[
                    "CONTRACTS_MUST_BE_ITERABLE"
                ],
            )

        input_count = len(
            contract_list
        )

        if input_count == 0:
            return self._blocked_result(
                reason="EMPTY_CONTRACT_COLLECTION",
                errors=[
                    "NO_CONTRACTS_PROVIDED"
                ],
                input_count=0,
            )

        valid_contracts = []
        rejected_contracts = []

        for index, contract in enumerate(
            contract_list
        ):
            normalized, errors = (
                self._normalize_contract(
                    contract
                )
            )

            if normalized is None:

                symbol = None

                if isinstance(
                    contract,
                    dict,
                ):
                    symbol = contract.get(
                        "tradingsymbol"
                    )

                rejected_contracts.append(
                    {
                        "index": index,
                        "tradingsymbol":
                            symbol,
                        "errors":
                            list(errors),
                    }
                )

                continue

            valid_contracts.append(
                normalized
            )

        if not valid_contracts:
            return self._blocked_result(
                reason="NO_VALID_ENRICHED_CONTRACTS",
                errors=[
                    "ALL_CONTRACTS_REJECTED"
                ],
                warnings=[
                    {
                        "rejected_contracts":
                            rejected_contracts
                    }
                ],
                input_count=input_count,
            )

        # ----------------------------------------------------
        # STRIKE MAP
        # ----------------------------------------------------

        strike_map = (
            self._build_strike_map(
                valid_contracts
            )
        )

        all_strikes = sorted(
            strike_map.keys()
        )

        complete_pair_strikes = (
            self._complete_pair_strikes(
                strike_map
            )
        )

        if (
            len(complete_pair_strikes)
            < self.minimum_complete_pairs
        ):
            return self._blocked_result(
                reason="INSUFFICIENT_COMPLETE_OPTION_PAIRS",
                errors=[
                    "NOT_ENOUGH_CE_PE_PAIRS"
                ],
                warnings=[
                    {
                        "complete_pair_count":
                            len(
                                complete_pair_strikes
                            ),
                        "required":
                            self.minimum_complete_pairs,
                    }
                ],
                input_count=input_count,
            )

        # ----------------------------------------------------
        # REFERENCE PRICE
        # ----------------------------------------------------

        (
            reference_price,
            reference_source,
        ) = self._resolve_reference_price(
            contracts=valid_contracts,
            spot_price=spot_price,
            implied_forward=implied_forward,
        )

        if (
            reference_price is None
            or reference_price <= 0
        ):
            return self._blocked_result(
                reason="REFERENCE_PRICE_UNAVAILABLE",
                errors=[
                    "SPOT_OR_IMPLIED_FORWARD_REQUIRED"
                ],
                input_count=input_count,
            )

        # ----------------------------------------------------
        # STRIKE STEP / ATM
        # ----------------------------------------------------

        strike_step = (
            self._detect_strike_step(
                complete_pair_strikes
            )
        )

        atm_strike = (
            self._find_atm_strike(
                complete_pair_strikes,
                reference_price,
            )
        )

        if atm_strike is None:
            return self._blocked_result(
                reason="ATM_STRIKE_UNAVAILABLE",
                errors=[
                    "CANNOT_DETERMINE_ATM_STRIKE"
                ],
                input_count=input_count,
            )

        # ----------------------------------------------------
        # ANALYTICS
        # ----------------------------------------------------

        atm_straddle = (
            self._calculate_atm_straddle(
                strike_map=strike_map,
                atm_strike=atm_strike,
                reference_price=reference_price,
            )
        )

        oi_structure = (
            self._calculate_oi_structure(
                valid_contracts
            )
        )

        iv_structure = (
            self._calculate_iv_structure(
                strike_map=strike_map,
                complete_pair_strikes=(
                    complete_pair_strikes
                ),
                atm_strike=atm_strike,
                strike_step=strike_step,
            )
        )

        greeks_structure = (
            self._calculate_greeks_structure(
                contracts=valid_contracts,
                strike_map=strike_map,
            )
        )

        market_structure = (
            self._calculate_market_structure(
                oi_structure
            )
        )

        data_quality = (
            self._calculate_data_quality(
                input_count=input_count,
                valid_contract_count=len(
                    valid_contracts
                ),
                complete_pair_count=len(
                    complete_pair_strikes
                ),
                contracts=valid_contracts,
                atm_straddle=atm_straddle,
            )
        )

        warnings = list(
            data_quality.get(
                "warnings",
                [],
            )
        )

        if rejected_contracts:
            warnings.append(
                "SOME_INPUT_CONTRACTS_REJECTED"
            )

        # ----------------------------------------------------
        # PRICING / GREEKS SEMANTICS
        # ----------------------------------------------------

        pricing_modes = sorted(
            {
                str(
                    contract.get(
                        "pricing_mode",
                        "",
                    )
                ).strip().upper()
                for contract
                in valid_contracts
                if contract.get(
                    "pricing_mode"
                )
            }
        )

        delta_bases = sorted(
            {
                str(
                    contract.get(
                        "delta_basis",
                        "",
                    )
                ).strip().upper()
                for contract
                in valid_contracts
                if contract.get(
                    "delta_basis"
                )
            }
        )

        gamma_bases = sorted(
            {
                str(
                    contract.get(
                        "gamma_basis",
                        "",
                    )
                ).strip().upper()
                for contract
                in valid_contracts
                if contract.get(
                    "gamma_basis"
                )
            }
        )

        analytics = {
            # --------------------------------------------
            # REFERENCE / STRUCTURE
            # --------------------------------------------
            "reference_price":
                reference_price,

            "reference_source":
                reference_source,

            "spot_price":
                self._safe_float(
                    spot_price
                ),

            "implied_forward":
                (
                    self._safe_float(
                        implied_forward
                    )
                    if implied_forward
                    is not None
                    else self._median(
                        [
                            contract.get(
                                "implied_forward"
                            )
                            for contract
                            in valid_contracts
                            if self._safe_float(
                                contract.get(
                                    "implied_forward"
                                )
                            )
                            is not None
                        ]
                    )
                ),

            "atm_strike":
                atm_strike,

            "strike_step":
                strike_step,

            "minimum_strike":
                min(all_strikes),

            "maximum_strike":
                max(all_strikes),

            "complete_pair_count":
                len(
                    complete_pair_strikes
                ),

            "complete_pair_strikes":
                list(
                    complete_pair_strikes
                ),

            # --------------------------------------------
            # ATM / EXPECTED MOVE
            # --------------------------------------------
            "atm_straddle":
                atm_straddle,

            # --------------------------------------------
            # OI
            # --------------------------------------------
            "oi":
                oi_structure,

            # --------------------------------------------
            # IV
            # --------------------------------------------
            "iv":
                iv_structure,

            # --------------------------------------------
            # GREEKS
            # --------------------------------------------
            "greeks":
                greeks_structure,

            # --------------------------------------------
            # MARKET STRUCTURE
            # --------------------------------------------
            "market_structure":
                market_structure,

            # --------------------------------------------
            # SEMANTICS
            # --------------------------------------------
            "pricing_modes":
                pricing_modes,

            "delta_bases":
                delta_bases,

            "gamma_bases":
                gamma_bases,
        }

        result = {
            "intelligence_permission":
                "ALLOW",

            "intelligence_allowed":
                True,

            "reason":
                "OPTION_MARKET_INTELLIGENCE_CALCULATED",

            "input_count":
                input_count,

            "valid_contract_count":
                len(
                    valid_contracts
                ),

            "rejected_contract_count":
                len(
                    rejected_contracts
                ),

            "complete_pair_count":
                len(
                    complete_pair_strikes
                ),

            "rejected_contracts":
                rejected_contracts,

            "warnings":
                warnings,

            "errors":
                [],

            "data_quality":
                data_quality,

            "analytics":
                analytics,
        }

        # Explicitly attach immutable authority denial.
        result.update(
            self._authority_flags()
        )

        return result

    # ========================================================
    # CONSUME OPTION GREEKS ENGINE RESULT
    # ========================================================

    def analyze_greeks_result(
        self,
        greeks_result,
        spot_price=None,
        implied_forward=None,
    ):
        """
        Convenience integration method for the exact result
        returned by OptionGreeksEngine.enrich_option_chain().

        The Greeks engine result is expected to contain:

            greeks_permission
            greeks_allowed
            contracts
            pricing_mode
            implied_forward
            broker_order_allowed

        A blocked Greeks result cannot become an allowed
        intelligence result.
        """

        if not isinstance(
            greeks_result,
            dict,
        ):
            return self._blocked_result(
                reason="INVALID_GREEKS_RESULT",
                errors=[
                    "GREEKS_RESULT_MUST_BE_DICTIONARY"
                ],
            )

        if (
            greeks_result.get(
                "greeks_permission"
            )
            != "ALLOW"
            or greeks_result.get(
                "greeks_allowed"
            )
            is not True
        ):
            return self._blocked_result(
                reason="UPSTREAM_GREEKS_BLOCKED",
                errors=[
                    str(
                        greeks_result.get(
                            "reason",
                            "UNKNOWN_GREEKS_BLOCK",
                        )
                    )
                ],
            )

        # Critical authority rule:
        # this engine never inherits broker authority,
        # even if malformed upstream data claims otherwise.

        contracts = (
            greeks_result.get(
                "contracts"
            )
        )

        upstream_forward = (
            self._safe_float(
                greeks_result.get(
                    "implied_forward"
                )
            )
        )

        if implied_forward is None:
            implied_forward = (
                upstream_forward
            )

        result = (
            self.analyze_option_chain(
                contracts=contracts,
                spot_price=spot_price,
                implied_forward=implied_forward,
            )
        )

        result[
            "upstream_greeks_permission"
        ] = greeks_result.get(
            "greeks_permission"
        )

        result[
            "upstream_greeks_allowed"
        ] = greeks_result.get(
            "greeks_allowed"
        )

        result[
            "upstream_pricing_mode"
        ] = greeks_result.get(
            "pricing_mode"
        )

        # Reassert authority invariants.
        result.update(
            self._authority_flags()
        )

        return result