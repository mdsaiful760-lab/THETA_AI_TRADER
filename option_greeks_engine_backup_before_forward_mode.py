# ============================================================
# THETA AI TRADER — OPTION GREEKS ENGINE
# ============================================================

import math
from datetime import date, datetime, time, timezone


class OptionGreeksEngine:
    """
    Analytical options-pricing and Greeks engine.

    Responsibilities:
        - Black-Scholes option pricing
        - Implied-volatility estimation
        - Delta
        - Gamma
        - Theta
        - Vega
        - Time-to-expiry calculation
        - Safe validation / rejection of invalid inputs

    This engine has:
        NO contract-selection authority
        NO trade-decision authority
        NO risk authority
        NO position-sizing authority
        NO broker/order authority
    """

    VALID_OPTION_TYPES = {"CE", "PE"}

    def __init__(
        self,
        risk_free_rate=0.06,
        dividend_yield=0.0,
        expiry_hour=15,
        expiry_minute=30,
        iv_min=0.0001,
        iv_max=5.0,
        iv_tolerance=0.0001,
        iv_max_iterations=200,
    ):
        self.risk_free_rate = float(risk_free_rate)
        self.dividend_yield = float(dividend_yield)

        self.expiry_hour = int(expiry_hour)
        self.expiry_minute = int(expiry_minute)

        self.iv_min = float(iv_min)
        self.iv_max = float(iv_max)
        self.iv_tolerance = float(iv_tolerance)
        self.iv_max_iterations = int(iv_max_iterations)

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

    @staticmethod
    def _normalize_option_type(value):
        if value is None:
            return ""

        return str(value).strip().upper()

    # ========================================================
    # NORMAL DISTRIBUTION
    # ========================================================

    @staticmethod
    def _normal_pdf(x):
        return (
            math.exp(-0.5 * x * x)
            / math.sqrt(2.0 * math.pi)
        )

    @staticmethod
    def _normal_cdf(x):
        return 0.5 * (
            1.0
            + math.erf(
                x / math.sqrt(2.0)
            )
        )

    # ========================================================
    # DATE / EXPIRY NORMALIZATION
    # ========================================================

    def _parse_expiry(self, expiry):
        if expiry is None:
            return None

        if isinstance(expiry, datetime):
            return expiry

        if isinstance(expiry, date):
            return datetime.combine(
                expiry,
                time(
                    self.expiry_hour,
                    self.expiry_minute,
                ),
            )

        text = str(expiry).strip()

        if not text:
            return None

        datetime_formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%d-%m-%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
        ]

        for fmt in datetime_formats:
            try:
                return datetime.strptime(
                    text,
                    fmt,
                )
            except ValueError:
                pass

        date_formats = [
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%Y/%m/%d",
            "%d/%m/%Y",
        ]

        for fmt in date_formats:
            try:
                parsed = datetime.strptime(
                    text,
                    fmt,
                ).date()

                return datetime.combine(
                    parsed,
                    time(
                        self.expiry_hour,
                        self.expiry_minute,
                    ),
                )

            except ValueError:
                pass

        return None

    # ========================================================
    # TIME TO EXPIRY
    # ========================================================

    def calculate_time_to_expiry(
        self,
        expiry,
        current_time=None,
    ):
        """
        Returns time-to-expiry in calendar years.

        This intentionally uses actual elapsed time rather than
        counting trading sessions. Black-Scholes time is expressed
        as annualized calendar time.
        """

        expiry_dt = self._parse_expiry(
            expiry
        )

        if expiry_dt is None:
            return None

        if current_time is None:
            current_time = datetime.now()

        if not isinstance(
            current_time,
            datetime,
        ):
            return None

        # Avoid mixing aware and naive datetime objects.
        if (
            expiry_dt.tzinfo is None
            and current_time.tzinfo
            is not None
        ):
            current_time = (
                current_time.replace(
                    tzinfo=None
                )
            )

        elif (
            expiry_dt.tzinfo is not None
            and current_time.tzinfo
            is None
        ):
            expiry_dt = (
                expiry_dt.replace(
                    tzinfo=None
                )
            )

        seconds = (
            expiry_dt - current_time
        ).total_seconds()

        if seconds <= 0:
            return 0.0

        seconds_per_year = (
            365.0 * 24.0 * 60.0 * 60.0
        )

        return seconds / seconds_per_year

    # ========================================================
    # D1 / D2
    # ========================================================

    def _calculate_d1_d2(
        self,
        spot,
        strike,
        time_to_expiry,
        volatility,
        risk_free_rate=None,
        dividend_yield=None,
    ):
        spot = self._safe_float(spot)
        strike = self._safe_float(strike)
        time_to_expiry = self._safe_float(
            time_to_expiry
        )
        volatility = self._safe_float(
            volatility
        )

        if risk_free_rate is None:
            risk_free_rate = (
                self.risk_free_rate
            )

        if dividend_yield is None:
            dividend_yield = (
                self.dividend_yield
            )

        risk_free_rate = self._safe_float(
            risk_free_rate
        )
        dividend_yield = self._safe_float(
            dividend_yield
        )

        if (
            spot is None
            or strike is None
            or time_to_expiry is None
            or volatility is None
            or risk_free_rate is None
            or dividend_yield is None
            or spot <= 0
            or strike <= 0
            or time_to_expiry <= 0
            or volatility <= 0
        ):
            return None, None

        denominator = (
            volatility
            * math.sqrt(
                time_to_expiry
            )
        )

        if denominator <= 0:
            return None, None

        numerator = (
            math.log(
                spot / strike
            )
            + (
                risk_free_rate
                - dividend_yield
                + 0.5
                * volatility
                * volatility
            )
            * time_to_expiry
        )

        d1 = numerator / denominator

        d2 = (
            d1
            - volatility
            * math.sqrt(
                time_to_expiry
            )
        )

        return d1, d2

    # ========================================================
    # BLACK-SCHOLES PRICE
    # ========================================================

    def black_scholes_price(
        self,
        spot,
        strike,
        time_to_expiry,
        volatility,
        option_type,
        risk_free_rate=None,
        dividend_yield=None,
    ):
        option_type = (
            self._normalize_option_type(
                option_type
            )
        )

        if (
            option_type
            not in self.VALID_OPTION_TYPES
        ):
            return None

        spot = self._safe_float(spot)
        strike = self._safe_float(strike)
        time_to_expiry = self._safe_float(
            time_to_expiry
        )
        volatility = self._safe_float(
            volatility
        )

        if risk_free_rate is None:
            risk_free_rate = (
                self.risk_free_rate
            )

        if dividend_yield is None:
            dividend_yield = (
                self.dividend_yield
            )

        risk_free_rate = self._safe_float(
            risk_free_rate
        )
        dividend_yield = self._safe_float(
            dividend_yield
        )

        if (
            spot is None
            or strike is None
            or time_to_expiry is None
            or volatility is None
            or risk_free_rate is None
            or dividend_yield is None
            or spot <= 0
            or strike <= 0
            or time_to_expiry <= 0
            or volatility <= 0
        ):
            return None

        d1, d2 = (
            self._calculate_d1_d2(
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry,
                volatility=volatility,
                risk_free_rate=risk_free_rate,
                dividend_yield=dividend_yield,
            )
        )

        if d1 is None or d2 is None:
            return None

        discount_r = math.exp(
            -risk_free_rate
            * time_to_expiry
        )

        discount_q = math.exp(
            -dividend_yield
            * time_to_expiry
        )

        if option_type == "CE":
            price = (
                spot
                * discount_q
                * self._normal_cdf(d1)
                - strike
                * discount_r
                * self._normal_cdf(d2)
            )

        else:
            price = (
                strike
                * discount_r
                * self._normal_cdf(-d2)
                - spot
                * discount_q
                * self._normal_cdf(-d1)
            )

        if not math.isfinite(price):
            return None

        return max(
            0.0,
            price,
        )

    # ========================================================
    # THEORETICAL PRICE BOUNDS
    # ========================================================

    def _price_bounds(
        self,
        spot,
        strike,
        time_to_expiry,
        option_type,
        risk_free_rate=None,
        dividend_yield=None,
    ):
        if risk_free_rate is None:
            risk_free_rate = (
                self.risk_free_rate
            )

        if dividend_yield is None:
            dividend_yield = (
                self.dividend_yield
            )

        discount_r = math.exp(
            -risk_free_rate
            * time_to_expiry
        )

        discount_q = math.exp(
            -dividend_yield
            * time_to_expiry
        )

        discounted_spot = (
            spot * discount_q
        )

        discounted_strike = (
            strike * discount_r
        )

        if option_type == "CE":
            lower = max(
                0.0,
                discounted_spot
                - discounted_strike,
            )

            upper = discounted_spot

        else:
            lower = max(
                0.0,
                discounted_strike
                - discounted_spot,
            )

            upper = discounted_strike

        return lower, upper

    # ========================================================
    # IMPLIED VOLATILITY
    # ========================================================

    def calculate_implied_volatility(
        self,
        market_price,
        spot,
        strike,
        time_to_expiry,
        option_type,
        risk_free_rate=None,
        dividend_yield=None,
    ):
        """
        Implied volatility using bounded bisection.

        Bisection is slower than Newton-Raphson but considerably
        safer for an automated trading system because it does not
        depend on a stable Vega derivative at every iteration.
        """

        market_price = self._safe_float(
            market_price
        )
        spot = self._safe_float(spot)
        strike = self._safe_float(strike)
        time_to_expiry = self._safe_float(
            time_to_expiry
        )

        option_type = (
            self._normalize_option_type(
                option_type
            )
        )

        if risk_free_rate is None:
            risk_free_rate = (
                self.risk_free_rate
            )

        if dividend_yield is None:
            dividend_yield = (
                self.dividend_yield
            )

        risk_free_rate = self._safe_float(
            risk_free_rate
        )
        dividend_yield = self._safe_float(
            dividend_yield
        )

        if (
            market_price is None
            or spot is None
            or strike is None
            or time_to_expiry is None
            or risk_free_rate is None
            or dividend_yield is None
            or market_price <= 0
            or spot <= 0
            or strike <= 0
            or time_to_expiry <= 0
            or option_type
            not in self.VALID_OPTION_TYPES
        ):
            return None

        lower_bound, upper_bound = (
            self._price_bounds(
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry,
                option_type=option_type,
                risk_free_rate=risk_free_rate,
                dividend_yield=dividend_yield,
            )
        )

        # Price outside arbitrage bounds cannot have
        # a meaningful Black-Scholes IV.
        price_epsilon = max(
            self.iv_tolerance,
            1e-8,
        )

        if (
            market_price
            < lower_bound - price_epsilon
            or market_price
            > upper_bound + price_epsilon
        ):
            return None

        low_vol = self.iv_min
        high_vol = self.iv_max

        low_price = (
            self.black_scholes_price(
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry,
                volatility=low_vol,
                option_type=option_type,
                risk_free_rate=risk_free_rate,
                dividend_yield=dividend_yield,
            )
        )

        high_price = (
            self.black_scholes_price(
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry,
                volatility=high_vol,
                option_type=option_type,
                risk_free_rate=risk_free_rate,
                dividend_yield=dividend_yield,
            )
        )

        if (
            low_price is None
            or high_price is None
        ):
            return None

        # Requested price cannot be reached inside our
        # configured volatility search range.
        if (
            market_price
            < low_price - price_epsilon
            or market_price
            > high_price + price_epsilon
        ):
            return None

        for _ in range(
            self.iv_max_iterations
        ):
            mid_vol = (
                low_vol + high_vol
            ) / 2.0

            theoretical_price = (
                self.black_scholes_price(
                    spot=spot,
                    strike=strike,
                    time_to_expiry=time_to_expiry,
                    volatility=mid_vol,
                    option_type=option_type,
                    risk_free_rate=risk_free_rate,
                    dividend_yield=dividend_yield,
                )
            )

            if theoretical_price is None:
                return None

            difference = (
                theoretical_price
                - market_price
            )

            if (
                abs(difference)
                <= self.iv_tolerance
            ):
                return mid_vol

            if difference > 0:
                high_vol = mid_vol
            else:
                low_vol = mid_vol

        final_vol = (
            low_vol + high_vol
        ) / 2.0

        final_price = (
            self.black_scholes_price(
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry,
                volatility=final_vol,
                option_type=option_type,
                risk_free_rate=risk_free_rate,
                dividend_yield=dividend_yield,
            )
        )

        if final_price is None:
            return None

        if (
            abs(
                final_price
                - market_price
            )
            > max(
                self.iv_tolerance * 10.0,
                0.01,
            )
        ):
            return None

        return final_vol

    # ========================================================
    # CALCULATE GREEKS
    # ========================================================

    def calculate_greeks(
        self,
        spot,
        strike,
        time_to_expiry,
        volatility,
        option_type,
        risk_free_rate=None,
        dividend_yield=None,
    ):
        spot = self._safe_float(spot)
        strike = self._safe_float(strike)

        time_to_expiry = (
            self._safe_float(
                time_to_expiry
            )
        )

        volatility = self._safe_float(
            volatility
        )

        option_type = (
            self._normalize_option_type(
                option_type
            )
        )

        if risk_free_rate is None:
            risk_free_rate = (
                self.risk_free_rate
            )

        if dividend_yield is None:
            dividend_yield = (
                self.dividend_yield
            )

        risk_free_rate = self._safe_float(
            risk_free_rate
        )

        dividend_yield = self._safe_float(
            dividend_yield
        )

        if (
            spot is None
            or strike is None
            or time_to_expiry is None
            or volatility is None
            or risk_free_rate is None
            or dividend_yield is None
            or spot <= 0
            or strike <= 0
            or time_to_expiry <= 0
            or volatility <= 0
            or option_type
            not in self.VALID_OPTION_TYPES
        ):
            return None

        d1, d2 = (
            self._calculate_d1_d2(
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry,
                volatility=volatility,
                risk_free_rate=risk_free_rate,
                dividend_yield=dividend_yield,
            )
        )

        if d1 is None or d2 is None:
            return None

        sqrt_t = math.sqrt(
            time_to_expiry
        )

        discount_r = math.exp(
            -risk_free_rate
            * time_to_expiry
        )

        discount_q = math.exp(
            -dividend_yield
            * time_to_expiry
        )

        pdf_d1 = self._normal_pdf(
            d1
        )

        # ----------------------------------------------------
        # DELTA
        # ----------------------------------------------------

        if option_type == "CE":
            delta = (
                discount_q
                * self._normal_cdf(
                    d1
                )
            )

        else:
            delta = (
                discount_q
                * (
                    self._normal_cdf(
                        d1
                    )
                    - 1.0
                )
            )

        # ----------------------------------------------------
        # GAMMA
        # ----------------------------------------------------

        gamma = (
            discount_q
            * pdf_d1
            / (
                spot
                * volatility
                * sqrt_t
            )
        )

        # ----------------------------------------------------
        # VEGA
        #
        # Returned as price change for ONE volatility point.
        # Example:
        # IV 15% -> 16% = +1 volatility point.
        # ----------------------------------------------------

        vega = (
            spot
            * discount_q
            * pdf_d1
            * sqrt_t
            / 100.0
        )

        # ----------------------------------------------------
        # THETA
        #
        # Returned as approximate option-price change PER
        # CALENDAR DAY.
        # ----------------------------------------------------

        first_theta_term = (
            -spot
            * discount_q
            * pdf_d1
            * volatility
            / (
                2.0 * sqrt_t
            )
        )

        if option_type == "CE":
            annual_theta = (
                first_theta_term
                - risk_free_rate
                * strike
                * discount_r
                * self._normal_cdf(
                    d2
                )
                + dividend_yield
                * spot
                * discount_q
                * self._normal_cdf(
                    d1
                )
            )

        else:
            annual_theta = (
                first_theta_term
                + risk_free_rate
                * strike
                * discount_r
                * self._normal_cdf(
                    -d2
                )
                - dividend_yield
                * spot
                * discount_q
                * self._normal_cdf(
                    -d1
                )
            )

        theta = (
            annual_theta / 365.0
        )

        values = [
            delta,
            gamma,
            theta,
            vega,
        ]

        if not all(
            math.isfinite(value)
            for value in values
        ):
            return None

        return {
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
        }

    # ========================================================
    # CHOOSE MARKET PRICE FOR IV
    # ========================================================

    def choose_market_price(
        self,
        contract,
    ):
        """
        Prefer bid/ask midpoint because LTP may be stale.

        Falls back to LTP when a valid two-sided market
        is unavailable.
        """

        if not isinstance(
            contract,
            dict,
        ):
            return None, None

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

            if midpoint > 0:
                return midpoint, "MIDPOINT"

        if (
            ltp is not None
            and ltp > 0
        ):
            return ltp, "LTP"

        return None, None

    # ========================================================
    # ENRICH ONE NORMALIZED CONTRACT
    # ========================================================

    def enrich_contract(
        self,
        contract,
        spot_price,
        current_time=None,
        risk_free_rate=None,
        dividend_yield=None,
    ):
        """
        Calculates IV + Greeks and returns a COPY of the
        normalized contract.

        The original MarketDataAdapter contract is not mutated.
        """

        if not isinstance(
            contract,
            dict,
        ):
            return {
                "valid": False,
                "reason": "INVALID_CONTRACT_OBJECT",
                "errors": [
                    "CONTRACT_MUST_BE_DICTIONARY"
                ],
                "contract": None,
                "broker_order_allowed": False,
            }

        required_fields = [
            "tradingsymbol",
            "expiry",
            "strike",
            "option_type",
        ]

        missing_fields = [
            field
            for field in required_fields
            if contract.get(field) is None
        ]

        if missing_fields:
            return {
                "valid": False,
                "reason": "MISSING_CONTRACT_FIELDS",
                "errors": [
                    f"MISSING_{field.upper()}"
                    for field in missing_fields
                ],
                "contract": None,
                "broker_order_allowed": False,
            }

        spot = self._safe_float(
            spot_price
        )

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

        errors = []

        if (
            spot is None
            or spot <= 0
        ):
            errors.append(
                "INVALID_SPOT_PRICE"
            )

        if (
            strike is None
            or strike <= 0
        ):
            errors.append(
                "INVALID_STRIKE"
            )

        if (
            option_type
            not in self.VALID_OPTION_TYPES
        ):
            errors.append(
                "INVALID_OPTION_TYPE"
            )

        time_to_expiry = (
            self.calculate_time_to_expiry(
                expiry=contract.get(
                    "expiry"
                ),
                current_time=current_time,
            )
        )

        if time_to_expiry is None:
            errors.append(
                "INVALID_EXPIRY"
            )

        elif time_to_expiry <= 0:
            errors.append(
                "OPTION_EXPIRED"
            )

        market_price, price_source = (
            self.choose_market_price(
                contract
            )
        )

        if (
            market_price is None
            or market_price <= 0
        ):
            errors.append(
                "INVALID_OPTION_MARKET_PRICE"
            )

        if errors:
            return {
                "valid": False,
                "reason": "INVALID_GREEKS_INPUT",
                "errors": errors,
                "contract": None,
                "broker_order_allowed": False,
            }

        implied_volatility = (
            self.calculate_implied_volatility(
                market_price=market_price,
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry,
                option_type=option_type,
                risk_free_rate=risk_free_rate,
                dividend_yield=dividend_yield,
            )
        )

        if implied_volatility is None:
            return {
                "valid": False,
                "reason": "IMPLIED_VOLATILITY_NOT_SOLVABLE",
                "errors": [
                    "IV_CALCULATION_FAILED"
                ],
                "contract": None,
                "broker_order_allowed": False,
            }

        greeks = self.calculate_greeks(
            spot=spot,
            strike=strike,
            time_to_expiry=time_to_expiry,
            volatility=implied_volatility,
            option_type=option_type,
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
        )

        if greeks is None:
            return {
                "valid": False,
                "reason": "GREEKS_CALCULATION_FAILED",
                "errors": [
                    "GREEKS_NOT_FINITE_OR_INVALID"
                ],
                "contract": None,
                "broker_order_allowed": False,
            }

        enriched = dict(
            contract
        )

        # Decimal IV used internally.
        enriched[
            "iv_decimal"
        ] = implied_volatility

        # Percentage IV used by downstream/display layers.
        enriched[
            "iv"
        ] = (
            implied_volatility
            * 100.0
        )

        enriched[
            "delta"
        ] = greeks[
            "delta"
        ]

        enriched[
            "gamma"
        ] = greeks[
            "gamma"
        ]

        enriched[
            "theta"
        ] = greeks[
            "theta"
        ]

        enriched[
            "vega"
        ] = greeks[
            "vega"
        ]

        enriched[
            "greeks_market_price"
        ] = market_price

        enriched[
            "greeks_price_source"
        ] = price_source

        enriched[
            "time_to_expiry_years"
        ] = time_to_expiry

        enriched[
            "risk_free_rate"
        ] = (
            self.risk_free_rate
            if risk_free_rate is None
            else float(
                risk_free_rate
            )
        )

        enriched[
            "dividend_yield"
        ] = (
            self.dividend_yield
            if dividend_yield is None
            else float(
                dividend_yield
            )
        )

        return {
            "valid": True,
            "reason": "GREEKS_CALCULATED",
            "errors": [],
            "contract": enriched,

            # Explicit invariant.
            "broker_order_allowed": False,
        }

    # ========================================================
    # ENRICH COMPLETE OPTION CHAIN
    # ========================================================

    def enrich_option_chain(
        self,
        contracts,
        spot_price,
        current_time=None,
        risk_free_rate=None,
        dividend_yield=None,
    ):
        if contracts is None:
            return {
                "greeks_permission": "BLOCK",
                "greeks_allowed": False,
                "reason": "CONTRACTS_REQUIRED",
                "input_count": 0,
                "enriched_count": 0,
                "rejected_count": 0,
                "contracts": [],
                "rejections": [],
                "broker_order_allowed": False,
            }

        try:
            contract_list = list(
                contracts
            )

        except TypeError:
            return {
                "greeks_permission": "BLOCK",
                "greeks_allowed": False,
                "reason": "INVALID_CONTRACT_COLLECTION",
                "input_count": 0,
                "enriched_count": 0,
                "rejected_count": 0,
                "contracts": [],
                "rejections": [],
                "broker_order_allowed": False,
            }

        enriched_contracts = []
        rejections = []

        for contract in contract_list:
            result = self.enrich_contract(
                contract=contract,
                spot_price=spot_price,
                current_time=current_time,
                risk_free_rate=risk_free_rate,
                dividend_yield=dividend_yield,
            )

            if not result["valid"]:
                symbol = None

                if isinstance(
                    contract,
                    dict,
                ):
                    symbol = contract.get(
                        "tradingsymbol"
                    )

                rejections.append(
                    {
                        "tradingsymbol": symbol,
                        "reason": result[
                            "reason"
                        ],
                        "errors": list(
                            result[
                                "errors"
                            ]
                        ),
                    }
                )

                continue

            enriched_contracts.append(
                result["contract"]
            )

        if not enriched_contracts:
            return {
                "greeks_permission": "BLOCK",
                "greeks_allowed": False,
                "reason": "NO_VALID_GREEKS_CONTRACTS",
                "input_count": len(
                    contract_list
                ),
                "enriched_count": 0,
                "rejected_count": len(
                    rejections
                ),
                "contracts": [],
                "rejections": rejections,
                "broker_order_allowed": False,
            }

        # Preserve deterministic downstream ordering.
        enriched_contracts.sort(
            key=lambda item: (
                str(
                    item.get(
                        "expiry",
                        "",
                    )
                ),
                float(
                    item.get(
                        "strike",
                        0.0,
                    )
                ),
                str(
                    item.get(
                        "option_type",
                        "",
                    )
                ),
                str(
                    item.get(
                        "tradingsymbol",
                        "",
                    )
                ),
            )
        )

        return {
            "greeks_permission": "ALLOW",
            "greeks_allowed": True,
            "reason": "OPTION_CHAIN_ENRICHED",
            "input_count": len(
                contract_list
            ),
            "enriched_count": len(
                enriched_contracts
            ),
            "rejected_count": len(
                rejections
            ),
            "contracts": enriched_contracts,
            "rejections": rejections,

            # Greeks engine has no execution authority.
            "broker_order_allowed": False,
        }