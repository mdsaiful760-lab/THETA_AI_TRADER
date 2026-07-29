# ============================================================
# THETA AI TRADER — GREEKS ENGINE FOUNDATION TEST
# ============================================================

from datetime import datetime

from greeks_engine import GreeksEngine


print("=" * 60)
print("🧮 THETA AI TRADER — GREEKS ENGINE TEST")
print("=" * 60)


# ============================================================
# CREATE ENGINE
# ============================================================

engine = GreeksEngine(
    risk_free_rate=0.06
)


# ============================================================
# FIXED TEST INPUTS
# ============================================================

spot = 24000.0
strike = 24000.0

current_time = datetime(
    2026,
    7,
    29,
    10,
    0,
)

expiry = "2026-08-04"

volatility = 0.15


# ============================================================
# TIME TO EXPIRY
# ============================================================

time_to_expiry = engine.time_to_expiry(
    expiry,
    current_time=current_time,
)


# ============================================================
# D1 / D2
# ============================================================

d1, d2 = engine.calculate_d1_d2(
    spot=spot,
    strike=strike,
    time_to_expiry=time_to_expiry,
    volatility=volatility,
)


# ============================================================
# OPTION PRICES
# ============================================================

call_price = engine.option_price(
    spot=spot,
    strike=strike,
    time_to_expiry=time_to_expiry,
    volatility=volatility,
    option_type="CE",
)

put_price = engine.option_price(
    spot=spot,
    strike=strike,
    time_to_expiry=time_to_expiry,
    volatility=volatility,
    option_type="PE",
)


# ============================================================
# IMPLIED VOLATILITY RECOVERY TEST
# ============================================================

call_iv = engine.implied_volatility(
    market_price=call_price,
    spot=spot,
    strike=strike,
    time_to_expiry=time_to_expiry,
    option_type="CE",
)

put_iv = engine.implied_volatility(
    market_price=put_price,
    spot=spot,
    strike=strike,
    time_to_expiry=time_to_expiry,
    option_type="PE",
)

# ============================================================
# GREEKS CALCULATION TEST
# ============================================================

call_greeks = engine.calculate_greeks(
    spot=spot,
    strike=strike,
    time_to_expiry=time_to_expiry,
    volatility=call_iv,
    option_type="CE",
)

put_greeks = engine.calculate_greeks(
    spot=spot,
    strike=strike,
    time_to_expiry=time_to_expiry,
    volatility=put_iv,
    option_type="PE",
)

# ============================================================
# OUTPUT
# ============================================================

print(f"Spot              : {spot:.2f}")
print(f"Strike            : {strike:.2f}")
print(f"Expiry            : {expiry}")
print(f"Current Time      : {current_time}")
print(f"Volatility        : {volatility * 100:.2f}%")

print("-" * 60)

print(
    f"Time to Expiry    : "
    f"{time_to_expiry:.8f} years"
)

print(f"D1                : {d1:.6f}")
print(f"D2                : {d2:.6f}")

print("-" * 60)

print(
    f"Theoretical CE    : "
    f"{call_price:.2f}"
)

print(
    f"Theoretical PE    : "
    f"{put_price:.2f}"
)

print("-" * 60)

print(
    f"Recovered CE IV   : "
    f"{call_iv * 100:.4f}%"
)

print(
    f"Recovered PE IV   : "
    f"{put_iv * 100:.4f}%"
)

print("-" * 60)

print("CALL GREEKS")
print(
    f"Delta             : "
    f"{call_greeks['delta']:.6f}"
)
print(
    f"Gamma             : "
    f"{call_greeks['gamma']:.8f}"
)
print(
    f"Theta / Day       : "
    f"{call_greeks['theta']:.4f}"
)
print(
    f"Vega / 1% IV      : "
    f"{call_greeks['vega']:.4f}"
)

print("-" * 60)

print("PUT GREEKS")
print(
    f"Delta             : "
    f"{put_greeks['delta']:.6f}"
)
print(
    f"Gamma             : "
    f"{put_greeks['gamma']:.8f}"
)
print(
    f"Theta / Day       : "
    f"{put_greeks['theta']:.4f}"
)
print(
    f"Vega / 1% IV      : "
    f"{put_greeks['vega']:.4f}"
)


# ============================================================
# BASIC SANITY CHECKS
# ============================================================

assert time_to_expiry > 0
assert call_price > 0
assert put_price > 0

assert d1 > d2

assert abs(
    call_iv - volatility
) < 0.001

assert abs(
    put_iv - volatility
) < 0.001

# Call delta must be positive
assert 0.0 < call_greeks["delta"] < 1.0

# Put delta must be negative
assert -1.0 < put_greeks["delta"] < 0.0

# ATM call and put delta should differ by approximately 1
assert abs(
    (
        call_greeks["delta"]
        - put_greeks["delta"]
    )
    - 1.0
) < 0.001

# Gamma must be positive
assert call_greeks["gamma"] > 0
assert put_greeks["gamma"] > 0

# CE and PE gamma should be effectively identical
assert abs(
    call_greeks["gamma"]
    - put_greeks["gamma"]
) < 0.000001

# Vega must be positive
assert call_greeks["vega"] > 0
assert put_greeks["vega"] > 0

# For this test case both options should have negative daily theta
assert call_greeks["theta"] < 0
assert put_greeks["theta"] < 0

print("-" * 60)
print("Foundation Checks : PASS")
print("=" * 60)