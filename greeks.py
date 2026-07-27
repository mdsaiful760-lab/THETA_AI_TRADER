import math
from scipy.stats import norm
from scipy.optimize import brentq


def d1_d2(spot, strike, time_to_expiry, rate, volatility):
    d1 = (
        math.log(spot / strike)
        + (rate + 0.5 * volatility ** 2) * time_to_expiry
    ) / (volatility * math.sqrt(time_to_expiry))

    d2 = d1 - volatility * math.sqrt(time_to_expiry)

    return d1, d2


def black_scholes_price(
    spot,
    strike,
    time_to_expiry,
    rate,
    volatility,
    option_type
):
    d1, d2 = d1_d2(
        spot,
        strike,
        time_to_expiry,
        rate,
        volatility
    )

    if option_type.upper() == "CE":
        return (
            spot * norm.cdf(d1)
            - strike
            * math.exp(-rate * time_to_expiry)
            * norm.cdf(d2)
        )

    elif option_type.upper() == "PE":
        return (
            strike
            * math.exp(-rate * time_to_expiry)
            * norm.cdf(-d2)
            - spot * norm.cdf(-d1)
        )

    raise ValueError("Option type must be CE or PE")


def implied_volatility(
    market_price,
    spot,
    strike,
    time_to_expiry,
    rate,
    option_type
):
    if market_price <= 0:
        return None

    def objective(volatility):
        return (
            black_scholes_price(
                spot,
                strike,
                time_to_expiry,
                rate,
                volatility,
                option_type
            )
            - market_price
        )

    try:
        return brentq(
            objective,
            0.0001,
            5.0,
            maxiter=200
        )

    except (ValueError, RuntimeError):
        return None


def calculate_greeks(
    spot,
    strike,
    time_to_expiry,
    rate,
    volatility,
    option_type
):
    d1, d2 = d1_d2(
        spot,
        strike,
        time_to_expiry,
        rate,
        volatility
    )

    option_type = option_type.upper()

    if option_type == "CE":
        delta = norm.cdf(d1)

    elif option_type == "PE":
        delta = norm.cdf(d1) - 1

    else:
        raise ValueError("Option type must be CE or PE")

    gamma = (
        norm.pdf(d1)
        / (
            spot
            * volatility
            * math.sqrt(time_to_expiry)
        )
    )

    vega = (
        spot
        * norm.pdf(d1)
        * math.sqrt(time_to_expiry)
        / 100
    )

    first_term = (
        -(spot * norm.pdf(d1) * volatility)
        / (2 * math.sqrt(time_to_expiry))
    )

    if option_type == "CE":
        theta = (
            first_term
            - rate
            * strike
            * math.exp(-rate * time_to_expiry)
            * norm.cdf(d2)
        )

    else:
        theta = (
            first_term
            + rate
            * strike
            * math.exp(-rate * time_to_expiry)
            * norm.cdf(-d2)
        )

    theta = theta / 365

    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega
    }


if __name__ == "__main__":

    spot = 23767.45
    strike = 23800
    time_to_expiry = 4 / 365
    rate = 0.055
    volatility = 0.15

    print("=" * 60)
    print("🚀 THETA AI TRADER — GREEKS ENGINE TEST")
    print("=" * 60)

    greeks = calculate_greeks(
        spot,
        strike,
        time_to_expiry,
        rate,
        volatility,
        "CE"
    )

    print(f"\nSpot   : {spot}")
    print(f"Strike : {strike}")
    print("IV     : 15.00%")

    print("\nOPTION GREEKS")
    print("-" * 40)

    print(f"Delta : {greeks['delta']:.4f}")
    print(f"Gamma : {greeks['gamma']:.6f}")
    print(f"Theta : {greeks['theta']:.2f} / day")
    print(f"Vega  : {greeks['vega']:.2f}")

    print("\n✅ GREEKS ENGINE WORKING")