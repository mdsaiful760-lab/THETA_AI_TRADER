# ============================================================
# THETA AI TRADER
# INDEX OPTION FORWARD ENGINE
# ============================================================

import math
from statistics import median


class IndexOptionForwardEngine:
    """
    Estimates an index implied forward from matched CE/PE prices.

    Put-call parity:

        C - P = exp(-rT) * (F - K)

    Therefore:

        F = K + exp(rT) * (C - P)

    Responsibilities:
        - Match CE/PE contracts by strike
        - Select usable market prices
        - Validate bid/ask quality
        - Estimate forward at each strike
        - Reject poor / unreasonable estimates
        - Robustly aggregate valid forward estimates
        - Produce quality diagnostics

    This engine has:
        NO contract-selection authority
        NO trade-decision authority
        NO risk authority
        NO position-sizing authority
        NO broker/order authority
    """

    def __init__(
        self,
        risk_free_rate=0.06,
        max_spread_pct=20.0,
        max_forward_deviation_pct=3.0,
        min_valid_pairs=2,
        preferred_strikes_each_side=3,
    ):
        self.risk_free_rate = float(risk_free_rate)
        self.max_spread_pct = float(max_spread_pct)
        self.max_forward_deviation_pct = float(
            max_forward_deviation_pct
        )
        self.min_valid_pairs = int(min_valid_pairs)
        self.preferred_strikes_each_side = int(
            preferred_strikes_each_side
        )

    # ========================================================
    # SAFE CONVERSION
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

    # ========================================================
    # MARKET PRICE
    # ========================================================

    def choose_market_price(self, contract):
        """
        Prefer bid/ask midpoint.

        LTP is used only when a valid two-sided market
        is unavailable.
        """

        if not isinstance(contract, dict):
            return {
                "valid": False,
                "price": None,
                "source": None,
                "bid": None,
                "ask": None,
                "spread": None,
                "spread_pct": None,
                "reason": "INVALID_CONTRACT",
            }

        bid = self._safe_float(
            contract.get("bid")
        )

        ask = self._safe_float(
            contract.get("ask")
        )

        ltp = self._safe_float(
            contract.get("ltp")
        )

        if (
            bid is not None
            and ask is not None
            and bid > 0
            and ask > 0
            and ask >= bid
        ):
            midpoint = (
                bid + ask
            ) / 2.0

            spread = ask - bid

            spread_pct = (
                spread / midpoint * 100.0
                if midpoint > 0
                else None
            )

            if (
                spread_pct is not None
                and spread_pct
                <= self.max_spread_pct
            ):
                return {
                    "valid": True,
                    "price": midpoint,
                    "source": "MIDPOINT",
                    "bid": bid,
                    "ask": ask,
                    "spread": spread,
                    "spread_pct": spread_pct,
                    "reason": "VALID_TWO_SIDED_MARKET",
                }

            return {
                "valid": False,
                "price": None,
                "source": None,
                "bid": bid,
                "ask": ask,
                "spread": spread,
                "spread_pct": spread_pct,
                "reason": "SPREAD_TOO_WIDE",
            }

        # We permit LTP fallback only if positive.
        if (
            ltp is not None
            and ltp > 0
        ):
            return {
                "valid": True,
                "price": ltp,
                "source": "LTP",
                "bid": bid,
                "ask": ask,
                "spread": None,
                "spread_pct": None,
                "reason": "LTP_FALLBACK",
            }

        return {
            "valid": False,
            "price": None,
            "source": None,
            "bid": bid,
            "ask": ask,
            "spread": None,
            "spread_pct": None,
            "reason": "NO_VALID_MARKET_PRICE",
        }

    # ========================================================
    # BUILD CE / PE PAIRS
    # ========================================================

    def build_strike_pairs(self, contracts):
        if contracts is None:
            return {}

        pairs = {}

        for contract in contracts:

            if not isinstance(contract, dict):
                continue

            strike = self._safe_float(
                contract.get("strike")
            )

            option_type = str(
                contract.get(
                    "option_type",
                    ""
                )
            ).strip().upper()

            if (
                strike is None
                or strike <= 0
                or option_type
                not in ("CE", "PE")
            ):
                continue

            pairs.setdefault(
                strike,
                {}
            )

            # Do not silently overwrite duplicates.
            if option_type in pairs[strike]:
                pairs[strike][
                    f"DUPLICATE_{option_type}"
                ] = True

                continue

            pairs[strike][
                option_type
            ] = contract

        return pairs

    # ========================================================
    # SINGLE-STRIKE FORWARD
    # ========================================================

    def estimate_forward_at_strike(
        self,
        strike,
        call_price,
        put_price,
        time_to_expiry,
        risk_free_rate=None,
    ):
        strike = self._safe_float(
            strike
        )

        call_price = self._safe_float(
            call_price
        )

        put_price = self._safe_float(
            put_price
        )

        time_to_expiry = self._safe_float(
            time_to_expiry
        )

        if risk_free_rate is None:
            risk_free_rate = (
                self.risk_free_rate
            )

        risk_free_rate = self._safe_float(
            risk_free_rate
        )

        if (
            strike is None
            or call_price is None
            or put_price is None
            or time_to_expiry is None
            or risk_free_rate is None
            or strike <= 0
            or call_price < 0
            or put_price < 0
            or time_to_expiry <= 0
        ):
            return None

        forward = (
            strike
            + math.exp(
                risk_free_rate
                * time_to_expiry
            )
            * (
                call_price
                - put_price
            )
        )

        if (
            not math.isfinite(
                forward
            )
            or forward <= 0
        ):
            return None

        return forward

    # ========================================================
    # ATM / REFERENCE STRIKE
    # ========================================================

    def find_reference_strike(
        self,
        pairs,
        spot_price,
    ):
        spot = self._safe_float(
            spot_price
        )

        if (
            spot is None
            or spot <= 0
            or not pairs
        ):
            return None

        complete_strikes = [
            strike
            for strike, legs
            in pairs.items()
            if (
                "CE" in legs
                and "PE" in legs
            )
        ]

        if not complete_strikes:
            return None

        return min(
            complete_strikes,
            key=lambda strike: abs(
                strike - spot
            ),
        )

    # ========================================================
    # ESTIMATE COMPLETE FORWARD
    # ========================================================

    def estimate_forward(
        self,
        contracts,
        spot_price,
        time_to_expiry,
        risk_free_rate=None,
    ):
        spot = self._safe_float(
            spot_price
        )

        time_to_expiry = self._safe_float(
            time_to_expiry
        )

        if risk_free_rate is None:
            risk_free_rate = (
                self.risk_free_rate
            )

        risk_free_rate = self._safe_float(
            risk_free_rate
        )

        validation_errors = []

        if (
            spot is None
            or spot <= 0
        ):
            validation_errors.append(
                "INVALID_SPOT_PRICE"
            )

        if (
            time_to_expiry is None
            or time_to_expiry <= 0
        ):
            validation_errors.append(
                "INVALID_TIME_TO_EXPIRY"
            )

        if risk_free_rate is None:
            validation_errors.append(
                "INVALID_RISK_FREE_RATE"
            )

        if contracts is None:
            validation_errors.append(
                "CONTRACTS_REQUIRED"
            )

        if validation_errors:
            return self._blocked_result(
                reason="INVALID_FORWARD_INPUT",
                validation_errors=validation_errors,
            )

        try:
            contract_list = list(
                contracts
            )

        except TypeError:
            return self._blocked_result(
                reason="INVALID_CONTRACT_COLLECTION",
                validation_errors=[
                    "CONTRACTS_NOT_ITERABLE"
                ],
            )

        pairs = self.build_strike_pairs(
            contract_list
        )

        reference_strike = (
            self.find_reference_strike(
                pairs=pairs,
                spot_price=spot,
            )
        )

        if reference_strike is None:
            return self._blocked_result(
                reason="NO_COMPLETE_CE_PE_PAIRS",
                input_count=len(
                    contract_list
                ),
            )

        complete_strikes = sorted(
            strike
            for strike, legs
            in pairs.items()
            if (
                "CE" in legs
                and "PE" in legs
            )
        )

        # Rank complete strikes by distance from ATM/reference.
        ranked_strikes = sorted(
            complete_strikes,
            key=lambda strike: (
                abs(
                    strike
                    - reference_strike
                ),
                strike,
            ),
        )

        desired_count = (
            self.preferred_strikes_each_side
            * 2
            + 1
        )

        preferred_strikes = set(
            ranked_strikes[
                :desired_count
            ]
        )

        estimates = []
        rejections = []

        for strike in complete_strikes:

            legs = pairs[
                strike
            ]

            if (
                legs.get(
                    "DUPLICATE_CE"
                )
                or legs.get(
                    "DUPLICATE_PE"
                )
            ):
                rejections.append(
                    {
                        "strike": strike,
                        "reason": (
                            "DUPLICATE_OPTION_LEG"
                        ),
                    }
                )
                continue

            ce = legs["CE"]
            pe = legs["PE"]

            ce_market = (
                self.choose_market_price(
                    ce
                )
            )

            pe_market = (
                self.choose_market_price(
                    pe
                )
            )

            if not ce_market[
                "valid"
            ]:
                rejections.append(
                    {
                        "strike": strike,
                        "reason": (
                            "INVALID_CE_MARKET"
                        ),
                        "detail": (
                            ce_market[
                                "reason"
                            ]
                        ),
                    }
                )
                continue

            if not pe_market[
                "valid"
            ]:
                rejections.append(
                    {
                        "strike": strike,
                        "reason": (
                            "INVALID_PE_MARKET"
                        ),
                        "detail": (
                            pe_market[
                                "reason"
                            ]
                        ),
                    }
                )
                continue

            forward = (
                self.estimate_forward_at_strike(
                    strike=strike,
                    call_price=ce_market[
                        "price"
                    ],
                    put_price=pe_market[
                        "price"
                    ],
                    time_to_expiry=time_to_expiry,
                    risk_free_rate=risk_free_rate,
                )
            )

            if forward is None:
                rejections.append(
                    {
                        "strike": strike,
                        "reason": (
                            "FORWARD_CALCULATION_FAILED"
                        ),
                    }
                )
                continue

            deviation_pct = (
                abs(
                    forward - spot
                )
                / spot
                * 100.0
            )

            if (
                deviation_pct
                > self.max_forward_deviation_pct
            ):
                rejections.append(
                    {
                        "strike": strike,
                        "reason": (
                            "FORWARD_TOO_FAR_FROM_SPOT"
                        ),
                        "forward": forward,
                        "deviation_pct": (
                            deviation_pct
                        ),
                    }
                )
                continue

            estimates.append(
                {
                    "strike": strike,
                    "forward": forward,
                    "deviation_pct": (
                        deviation_pct
                    ),
                    "ce_price": (
                        ce_market[
                            "price"
                        ]
                    ),
                    "pe_price": (
                        pe_market[
                            "price"
                        ]
                    ),
                    "ce_price_source": (
                        ce_market[
                            "source"
                        ]
                    ),
                    "pe_price_source": (
                        pe_market[
                            "source"
                        ]
                    ),
                    "ce_spread_pct": (
                        ce_market[
                            "spread_pct"
                        ]
                    ),
                    "pe_spread_pct": (
                        pe_market[
                            "spread_pct"
                        ]
                    ),
                    "preferred": (
                        strike
                        in preferred_strikes
                    ),
                }
            )

        if len(
            estimates
        ) < self.min_valid_pairs:
            return self._blocked_result(
                reason=(
                    "INSUFFICIENT_VALID_FORWARD_PAIRS"
                ),
                input_count=len(
                    contract_list
                ),
                pair_count=len(
                    complete_strikes
                ),
                valid_pair_count=len(
                    estimates
                ),
                rejected_pair_count=len(
                    rejections
                ),
                reference_strike=(
                    reference_strike
                ),
                estimates=estimates,
                rejections=rejections,
            )

        # Prefer strikes nearest ATM/reference.
        preferred_estimates = [
            item
            for item in estimates
            if item[
                "preferred"
            ]
        ]

        aggregation_pool = (
            preferred_estimates
            if len(
                preferred_estimates
            ) >= self.min_valid_pairs
            else estimates
        )

        initial_median = median(
            item[
                "forward"
            ]
            for item
            in aggregation_pool
        )

        # ----------------------------------------------------
        # ROBUST OUTLIER FILTER
        # ----------------------------------------------------

        absolute_deviations = [
            abs(
                item[
                    "forward"
                ]
                - initial_median
            )
            for item
            in aggregation_pool
        ]

        mad = median(
            absolute_deviations
        )

        if mad > 0:
            filtered_pool = [
                item
                for item
                in aggregation_pool
                if abs(
                    item[
                        "forward"
                    ]
                    - initial_median
                )
                <= 3.0 * mad
            ]

        else:
            filtered_pool = list(
                aggregation_pool
            )

        if len(
            filtered_pool
        ) < self.min_valid_pairs:
            filtered_pool = list(
                aggregation_pool
            )

        implied_forward = median(
            item[
                "forward"
            ]
            for item
            in filtered_pool
        )

        forward_values = [
            item[
                "forward"
            ]
            for item
            in filtered_pool
        ]

        forward_min = min(
            forward_values
        )

        forward_max = max(
            forward_values
        )

        forward_range = (
            forward_max
            - forward_min
        )

        forward_range_pct = (
            forward_range
            / implied_forward
            * 100.0
            if implied_forward > 0
            else None
        )

        basis = (
            implied_forward
            - spot
        )

        basis_pct = (
            basis
            / spot
            * 100.0
        )

        quality = (
            self._assess_quality(
                valid_pair_count=len(
                    filtered_pool
                ),
                forward_range_pct=(
                    forward_range_pct
                ),
                basis_pct=basis_pct,
            )
        )

        return {
            "forward_permission": "ALLOW",
            "forward_allowed": True,
            "reason": (
                "IMPLIED_FORWARD_ESTIMATED"
            ),

            "spot_price": spot,
            "implied_forward": (
                implied_forward
            ),
            "basis": basis,
            "basis_pct": basis_pct,

            "risk_free_rate": (
                risk_free_rate
            ),
            "time_to_expiry": (
                time_to_expiry
            ),

            "reference_strike": (
                reference_strike
            ),

            "input_count": len(
                contract_list
            ),

            "pair_count": len(
                complete_strikes
            ),

            "valid_pair_count": len(
                estimates
            ),

            "aggregation_pair_count": len(
                filtered_pool
            ),

            "rejected_pair_count": len(
                rejections
            ),

            "forward_min": (
                forward_min
            ),

            "forward_max": (
                forward_max
            ),

            "forward_range": (
                forward_range
            ),

            "forward_range_pct": (
                forward_range_pct
            ),

            "quality": quality,

            "estimates": estimates,
            "aggregation_estimates": (
                filtered_pool
            ),
            "rejections": rejections,

            # Explicit authority boundaries.
            "contract_selection_allowed": False,
            "trade_decision_allowed": False,
            "risk_allocation_allowed": False,
            "position_sizing_allowed": False,
            "broker_order_allowed": False,
        }

    # ========================================================
    # QUALITY ASSESSMENT
    # ========================================================

    def _assess_quality(
        self,
        valid_pair_count,
        forward_range_pct,
        basis_pct,
    ):
        """
        Diagnostic quality only.

        This is NOT a trade-confidence score.
        """

        if (
            forward_range_pct is None
            or basis_pct is None
        ):
            return "LOW"

        if (
            valid_pair_count >= 5
            and forward_range_pct <= 0.15
            and abs(
                basis_pct
            ) <= 1.0
        ):
            return "HIGH"

        if (
            valid_pair_count >= 3
            and forward_range_pct <= 0.50
            and abs(
                basis_pct
            ) <= 2.0
        ):
            return "MEDIUM"

        return "LOW"

    # ========================================================
    # BLOCK RESULT
    # ========================================================

    @staticmethod
    def _blocked_result(
        reason,
        validation_errors=None,
        input_count=0,
        pair_count=0,
        valid_pair_count=0,
        rejected_pair_count=0,
        reference_strike=None,
        estimates=None,
        rejections=None,
    ):
        return {
            "forward_permission": "BLOCK",
            "forward_allowed": False,
            "reason": reason,

            "spot_price": None,
            "implied_forward": None,
            "basis": None,
            "basis_pct": None,

            "reference_strike": (
                reference_strike
            ),

            "input_count": (
                input_count
            ),

            "pair_count": (
                pair_count
            ),

            "valid_pair_count": (
                valid_pair_count
            ),

            "aggregation_pair_count": 0,

            "rejected_pair_count": (
                rejected_pair_count
            ),

            "forward_min": None,
            "forward_max": None,
            "forward_range": None,
            "forward_range_pct": None,

            "quality": "LOW",

            "validation_errors": (
                validation_errors
                or []
            ),

            "estimates": (
                estimates
                or []
            ),

            "aggregation_estimates": [],

            "rejections": (
                rejections
                or []
            ),

            # A blocked analytical result has no authority.
            "contract_selection_allowed": False,
            "trade_decision_allowed": False,
            "risk_allocation_allowed": False,
            "position_sizing_allowed": False,
            "broker_order_allowed": False,
        }