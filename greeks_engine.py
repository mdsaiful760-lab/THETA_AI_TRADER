# ============================================================
# THETA AI TRADER — OPTION GREEKS ENGINE
# ============================================================

import math
from datetime import date, datetime


class GreeksEngine:
    """
    Calculates Black-Scholes option values and Greeks.

    This engine performs calculations only.
    It does NOT place orders.
    """

    def __init__(
        self,
        risk_free_rate=0.06,
    ):
        self.risk_free_rate = float(
            risk_free_rate
        )

    # --------------------------------------------------------
    # STANDARD NORMAL PDF
    # --------------------------------------------------------

    def normal_pdf(self, x):

        return (
            math.exp(
                -0.5 * x * x
            )
            / math.sqrt(
                2.0 * math.pi
            )
        )

    # --------------------------------------------------------
    # STANDARD NORMAL CDF
    # --------------------------------------------------------

    def normal_cdf(self, x):

        return 0.5 * (
            1.0
            + math.erf(
                x / math.sqrt(2.0)
            )
        )

    # --------------------------------------------------------
    # TIME TO EXPIRY
    # --------------------------------------------------------

    def time_to_expiry(
        self,
        expiry,
        current_time=None,
    ):
        """
        Return time to expiry in years.

        NIFTY options are treated as expiring
        at 15:30 on expiry day.
        """

        if current_time is None:
            current_time = datetime.now()

        if isinstance(expiry, str):
            expiry = datetime.strptime(
                expiry,
                "%Y-%m-%d",
            ).date()

        if isinstance(expiry, datetime):
            expiry = expiry.date()

        if not isinstance(expiry, date):
            raise ValueError(
                "Invalid expiry date"
            )

        expiry_datetime = datetime.combine(
            expiry,
            datetime.strptime(
                "15:30",
                "%H:%M",
            ).time(),
        )

        seconds_remaining = (
            expiry_datetime
            - current_time
        ).total_seconds()

        if seconds_remaining <= 0:
            raise ValueError(
                "Option has expired"
            )

        seconds_per_year = (
            365.0 * 24.0 * 60.0 * 60.0
        )

        return (
            seconds_remaining
            / seconds_per_year
        )

    # --------------------------------------------------------
    # D1 / D2
    # --------------------------------------------------------

    def calculate_d1_d2(
        self,
        spot,
        strike,
        time_to_expiry,
        volatility,
    ):

        spot = float(spot)
        strike = float(strike)
        time_to_expiry = float(
            time_to_expiry
        )
        volatility = float(volatility)

        if spot <= 0:
            raise ValueError(
                "Spot must be greater than zero"
            )

        if strike <= 0:
            raise ValueError(
                "Strike must be greater than zero"
            )

        if time_to_expiry <= 0:
            raise ValueError(
                "Time to expiry must be greater than zero"
            )

        if volatility <= 0:
            raise ValueError(
                "Volatility must be greater than zero"
            )

        sqrt_t = math.sqrt(
            time_to_expiry
        )

        d1 = (
            math.log(
                spot / strike
            )
            + (
                self.risk_free_rate
                + 0.5
                * volatility
                * volatility
            )
            * time_to_expiry
        ) / (
            volatility * sqrt_t
        )

        d2 = (
            d1
            - volatility * sqrt_t
        )

        return d1, d2

    # --------------------------------------------------------
    # BLACK-SCHOLES OPTION PRICE
    # --------------------------------------------------------

    def option_price(
        self,
        spot,
        strike,
        time_to_expiry,
        volatility,
        option_type,
    ):

        option_type = str(
            option_type
        ).upper()

        d1, d2 = self.calculate_d1_d2(
            spot,
            strike,
            time_to_expiry,
            volatility,
        )

        discount_factor = math.exp(
            -self.risk_free_rate
            * time_to_expiry
        )

        if option_type == "CE":

            price = (
                spot
                * self.normal_cdf(d1)
                - strike
                * discount_factor
                * self.normal_cdf(d2)
            )

        elif option_type == "PE":

            price = (
                strike
                * discount_factor
                * self.normal_cdf(-d2)
                - spot
                * self.normal_cdf(-d1)
            )

        else:
            raise ValueError(
                "Option type must be CE or PE"
            )

        return max(
            0.0,
            price,
        )


    # --------------------------------------------------------
    # IMPLIED VOLATILITY SOLVER
    # --------------------------------------------------------

    def implied_volatility(
        self,
        market_price,
        spot,
        strike,
        time_to_expiry,
        option_type,
        min_volatility=0.01,
        max_volatility=3.00,
        tolerance=0.01,
        max_iterations=100,
    ):
        """
        Estimate implied volatility from the observed
        option market price using binary search.

        Volatility is returned as a decimal:
        0.15 = 15%
        """

        market_price = float(market_price)
        spot = float(spot)
        strike = float(strike)
        time_to_expiry = float(time_to_expiry)

        if market_price <= 0:
            raise ValueError(
                "Market price must be greater than zero"
            )

        if spot <= 0:
            raise ValueError(
                "Spot must be greater than zero"
            )

        if strike <= 0:
            raise ValueError(
                "Strike must be greater than zero"
            )

        if time_to_expiry <= 0:
            raise ValueError(
                "Time to expiry must be greater than zero"
            )

        option_type = str(
            option_type
        ).upper()

        if option_type not in ("CE", "PE"):
            raise ValueError(
                "Option type must be CE or PE"
            )

        low_vol = float(min_volatility)
        high_vol = float(max_volatility)

        # Check whether market price can be represented
        # inside our volatility search range.
        low_price = self.option_price(
            spot=spot,
            strike=strike,
            time_to_expiry=time_to_expiry,
            volatility=low_vol,
            option_type=option_type,
        )

        high_price = self.option_price(
            spot=spot,
            strike=strike,
            time_to_expiry=time_to_expiry,
            volatility=high_vol,
            option_type=option_type,
        )

        if market_price < low_price - tolerance:
            raise ValueError(
                "Market price is below IV search range"
            )

        if market_price > high_price + tolerance:
            raise ValueError(
                "Market price is above IV search range"
            )

        for _ in range(max_iterations):

            mid_vol = (
                low_vol + high_vol
            ) / 2.0

            theoretical_price = (
                self.option_price(
                    spot=spot,
                    strike=strike,
                    time_to_expiry=time_to_expiry,
                    volatility=mid_vol,
                    option_type=option_type,
                )
            )

            difference = (
                theoretical_price
                - market_price
            )

            if abs(difference) <= tolerance:
                return mid_vol

            if theoretical_price > market_price:
                high_vol = mid_vol
            else:
                low_vol = mid_vol

        # Return best estimate if tolerance was not
        # reached within max_iterations.
        return (
            low_vol + high_vol
        ) / 2.0

    # --------------------------------------------------------
    # OPTION GREEKS
    # --------------------------------------------------------

    def calculate_greeks(
        self,
        spot,
        strike,
        time_to_expiry,
        volatility,
        option_type,
    ):
        """
        Calculate Black-Scholes Greeks.

        Returns:
        - Delta
        - Gamma
        - Theta per calendar day
        - Vega for a 1 percentage-point IV move
        """

        spot = float(spot)
        strike = float(strike)
        time_to_expiry = float(
            time_to_expiry
        )
        volatility = float(volatility)

        option_type = str(
            option_type
        ).upper()

        if option_type not in ("CE", "PE"):
            raise ValueError(
                "Option type must be CE or PE"
            )

        d1, d2 = self.calculate_d1_d2(
            spot=spot,
            strike=strike,
            time_to_expiry=time_to_expiry,
            volatility=volatility,
        )

        sqrt_t = math.sqrt(
            time_to_expiry
        )

        discount_factor = math.exp(
            -self.risk_free_rate
            * time_to_expiry
        )

        pdf_d1 = self.normal_pdf(
            d1
        )

        # ----------------------------------------------------
        # DELTA
        # ----------------------------------------------------

        if option_type == "CE":
            delta = self.normal_cdf(
                d1
            )

        else:
            delta = (
                self.normal_cdf(d1)
                - 1.0
            )

        # ----------------------------------------------------
        # GAMMA
        # ----------------------------------------------------

        gamma = (
            pdf_d1
            / (
                spot
                * volatility
                * sqrt_t
            )
        )

        # ----------------------------------------------------
        # VEGA
        # ----------------------------------------------------

        # Divide by 100 so Vega represents the
        # price change for a 1 percentage-point IV move.
        vega = (
            spot
            * pdf_d1
            * sqrt_t
        ) / 100.0

        # ----------------------------------------------------
        # THETA
        # ----------------------------------------------------

        common_theta = -(
            spot
            * pdf_d1
            * volatility
        ) / (
            2.0 * sqrt_t
        )

        if option_type == "CE":

            theta_annual = (
                common_theta
                - self.risk_free_rate
                * strike
                * discount_factor
                * self.normal_cdf(d2)
            )

        else:

            theta_annual = (
                common_theta
                + self.risk_free_rate
                * strike
                * discount_factor
                * self.normal_cdf(-d2)
            )

        # Convert annualized theta to calendar-day theta.
        theta_daily = (
            theta_annual / 365.0
        )

        return {
            "delta": delta,
            "gamma": gamma,
            "theta": theta_daily,
            "vega": vega,
        }
