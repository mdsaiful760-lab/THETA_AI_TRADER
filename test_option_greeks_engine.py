# ============================================================
# THETA AI TRADER
# OPTION GREEKS ENGINE — TEST SUITE
# ============================================================

import math
from copy import deepcopy
from datetime import datetime

from option_greeks_engine import OptionGreeksEngine


PASSED = 0
TOTAL = 0


# ============================================================
# DISPLAY HELPERS
# ============================================================

def line(char="=", length=78):
    print(char * length)


def heading(title):
    print()
    line()
    print(title)
    line()


# ============================================================
# ASSERTION HELPERS
# ============================================================

def assert_true(value, message):
    if value is not True:
        raise AssertionError(
            f"{message}\nExpected: True\nActual  : {value}"
        )


def assert_false(value, message):
    if value is not False:
        raise AssertionError(
            f"{message}\nExpected: False\nActual  : {value}"
        )


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            f"{message}\nExpected: {expected}\nActual  : {actual}"
        )


def assert_close(
    actual,
    expected,
    message,
    tolerance=0.0001,
):
    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}\n"
            f"Tolerance: {tolerance}"
        )


def assert_finite(value, message):
    if value is None or not math.isfinite(float(value)):
        raise AssertionError(
            f"{message}\nActual: {value}"
        )


# ============================================================
# CONTRACT FACTORY
# ============================================================

def make_contract(
    symbol="NIFTY26AUG25000CE",
    strike=25000,
    option_type="CE",
    expiry="2026-08-25",
    ltp=250.0,
    bid=249.5,
    ask=250.5,
):
    return {
        "underlying": "NIFTY",
        "exchange": "NFO",
        "tradingsymbol": symbol,
        "expiry": expiry,
        "strike": float(strike),
        "option_type": option_type,
        "lot_size": 75,
        "ltp": float(ltp),
        "bid": bid,
        "ask": ask,
        "volume": 100000,
        "open_interest": 500000,
        "delta": None,
        "iv": None,
        "gamma": None,
        "theta": None,
        "vega": None,
        "instrument_token": 123456,
        "exchange_token": 654321,
        "tick_size": 0.05,
    }


# ============================================================
# FIXED TEST TIME
# ============================================================

CURRENT_TIME = datetime(
    2026,
    8,
    20,
    10,
    0,
    0,
)


# ============================================================
# HELPER — CREATE MARKET PRICE FROM KNOWN IV
# ============================================================

def theoretical_contract(
    engine,
    symbol,
    strike,
    option_type,
    volatility,
    spot=25000.0,
):
    expiry = "2026-08-25"

    t = engine.calculate_time_to_expiry(
        expiry=expiry,
        current_time=CURRENT_TIME,
    )

    price = engine.black_scholes_price(
        spot=spot,
        strike=strike,
        time_to_expiry=t,
        volatility=volatility,
        option_type=option_type,
    )

    # Small symmetric spread around theoretical price.
    spread = min(
        1.0,
        max(0.10, price * 0.002),
    )

    bid = max(
        0.01,
        price - spread / 2.0,
    )

    ask = price + spread / 2.0

    return make_contract(
        symbol=symbol,
        strike=strike,
        option_type=option_type,
        expiry=expiry,
        ltp=price,
        bid=bid,
        ask=ask,
    )


# ============================================================
# TEST 1 — BLACK-SCHOLES CALL PRICE
# ============================================================

def test_call_price():
    engine = OptionGreeksEngine(
        risk_free_rate=0.05,
        dividend_yield=0.0,
    )

    price = engine.black_scholes_price(
        spot=100,
        strike=100,
        time_to_expiry=1.0,
        volatility=0.20,
        option_type="CE",
    )

    assert_close(
        price,
        10.4506,
        "Standard Black-Scholes call price incorrect",
        tolerance=0.001,
    )

    print("Call Price :", round(price, 6))
    print("✅ PASS — Standard CE Black-Scholes price correct")


# ============================================================
# TEST 2 — BLACK-SCHOLES PUT PRICE
# ============================================================

def test_put_price():
    engine = OptionGreeksEngine(
        risk_free_rate=0.05,
        dividend_yield=0.0,
    )

    price = engine.black_scholes_price(
        spot=100,
        strike=100,
        time_to_expiry=1.0,
        volatility=0.20,
        option_type="PE",
    )

    assert_close(
        price,
        5.5735,
        "Standard Black-Scholes put price incorrect",
        tolerance=0.001,
    )

    print("Put Price :", round(price, 6))
    print("✅ PASS — Standard PE Black-Scholes price correct")


# ============================================================
# TEST 3 — PUT-CALL PARITY
# ============================================================

def test_put_call_parity():
    engine = OptionGreeksEngine(
        risk_free_rate=0.06,
        dividend_yield=0.0,
    )

    spot = 25000
    strike = 25000
    t = 30 / 365
    vol = 0.15

    call = engine.black_scholes_price(
        spot, strike, t, vol, "CE"
    )

    put = engine.black_scholes_price(
        spot, strike, t, vol, "PE"
    )

    lhs = call - put

    rhs = (
        spot
        - strike
        * math.exp(
            -0.06 * t
        )
    )

    assert_close(
        lhs,
        rhs,
        "Put-call parity violated",
        tolerance=0.001,
    )

    print("Call - Put :", round(lhs, 6))
    print("Parity RHS :", round(rhs, 6))
    print("✅ PASS — Put-call parity maintained")


# ============================================================
# TEST 4 — IMPLIED VOLATILITY RECOVERY
# ============================================================

def test_iv_recovery():
    engine = OptionGreeksEngine()

    spot = 25000
    strike = 25000
    t = 20 / 365
    true_iv = 0.18

    market_price = engine.black_scholes_price(
        spot,
        strike,
        t,
        true_iv,
        "CE",
    )

    recovered_iv = (
        engine.calculate_implied_volatility(
            market_price=market_price,
            spot=spot,
            strike=strike,
            time_to_expiry=t,
            option_type="CE",
        )
    )

    assert_close(
        recovered_iv,
        true_iv,
        "IV solver failed to recover known volatility",
        tolerance=0.0001,
    )

    print("True IV      :", true_iv)
    print("Recovered IV :", recovered_iv)
    print("✅ PASS — Known IV recovered from option price")


# ============================================================
# TEST 5 — CALL DELTA RANGE
# ============================================================

def test_call_delta():
    engine = OptionGreeksEngine()

    greeks = engine.calculate_greeks(
        spot=25000,
        strike=25000,
        time_to_expiry=20 / 365,
        volatility=0.18,
        option_type="CE",
    )

    delta = greeks["delta"]

    assert_true(
        0 < delta < 1,
        "CE delta must remain between 0 and 1",
    )

    print("CE Delta :", delta)
    print("✅ PASS — CE delta has correct sign/range")


# ============================================================
# TEST 6 — PUT DELTA RANGE
# ============================================================

def test_put_delta():
    engine = OptionGreeksEngine()

    greeks = engine.calculate_greeks(
        spot=25000,
        strike=25000,
        time_to_expiry=20 / 365,
        volatility=0.18,
        option_type="PE",
    )

    delta = greeks["delta"]

    assert_true(
        -1 < delta < 0,
        "PE delta must remain between -1 and 0",
    )

    print("PE Delta :", delta)
    print("✅ PASS — PE delta has correct sign/range")


# ============================================================
# TEST 7 — GAMMA POSITIVE
# ============================================================

def test_gamma():
    engine = OptionGreeksEngine()

    greeks = engine.calculate_greeks(
        25000,
        25000,
        20 / 365,
        0.18,
        "CE",
    )

    assert_true(
        greeks["gamma"] > 0,
        "Gamma should be positive",
    )

    print("Gamma :", greeks["gamma"])
    print("✅ PASS — Gamma positive")


# ============================================================
# TEST 8 — VEGA POSITIVE
# ============================================================

def test_vega():
    engine = OptionGreeksEngine()

    greeks = engine.calculate_greeks(
        25000,
        25000,
        20 / 365,
        0.18,
        "CE",
    )

    assert_true(
        greeks["vega"] > 0,
        "Vega should be positive",
    )

    print("Vega :", greeks["vega"])
    print("✅ PASS — Vega positive")


# ============================================================
# TEST 9 — THETA FINITE
# ============================================================

def test_theta():
    engine = OptionGreeksEngine()

    greeks = engine.calculate_greeks(
        25000,
        25000,
        20 / 365,
        0.18,
        "CE",
    )

    assert_finite(
        greeks["theta"],
        "Theta must remain finite",
    )

    print("Theta :", greeks["theta"])
    print("✅ PASS — Theta finite")


# ============================================================
# TEST 10 — DEEP ITM / OTM DELTA
# ============================================================

def test_deep_delta_behavior():
    engine = OptionGreeksEngine()

    itm = engine.calculate_greeks(
        27000,
        24000,
        20 / 365,
        0.15,
        "CE",
    )

    otm = engine.calculate_greeks(
        24000,
        27000,
        20 / 365,
        0.15,
        "CE",
    )

    assert_true(
        itm["delta"] > otm["delta"],
        "Deep ITM call must have higher delta than OTM call",
    )

    print("ITM Delta :", itm["delta"])
    print("OTM Delta :", otm["delta"])
    print("✅ PASS — Delta behaves correctly across moneyness")


# ============================================================
# TEST 11 — MIDPOINT PREFERRED
# ============================================================

def test_midpoint_preferred():
    engine = OptionGreeksEngine()

    contract = make_contract(
        ltp=500,
        bid=199,
        ask=201,
    )

    price, source = engine.choose_market_price(
        contract
    )

    assert_close(
        price,
        200,
        "Bid/ask midpoint incorrect",
    )

    assert_equal(
        source,
        "MIDPOINT",
        "Midpoint should be preferred over LTP",
    )

    print("Price  :", price)
    print("Source :", source)
    print("✅ PASS — Bid/ask midpoint preferred over stale LTP")


# ============================================================
# TEST 12 — LTP FALLBACK
# ============================================================

def test_ltp_fallback():
    engine = OptionGreeksEngine()

    contract = make_contract(
        ltp=175,
        bid=None,
        ask=None,
    )

    price, source = engine.choose_market_price(
        contract
    )

    assert_close(
        price,
        175,
        "LTP fallback incorrect",
    )

    assert_equal(
        source,
        "LTP",
        "Expected LTP fallback",
    )

    print("Price  :", price)
    print("Source :", source)
    print("✅ PASS — LTP used when two-sided market unavailable")


# ============================================================
# TEST 13 — IMPOSSIBLE OPTION PRICE
# ============================================================

def test_impossible_price():
    engine = OptionGreeksEngine()

    iv = engine.calculate_implied_volatility(
        market_price=30000,
        spot=25000,
        strike=25000,
        time_to_expiry=20 / 365,
        option_type="CE",
    )

    assert_equal(
        iv,
        None,
        "Impossible option price must not produce IV",
    )

    print("IV Result :", iv)
    print("✅ PASS — Impossible market price rejected")


# ============================================================
# TEST 14 — EXPIRED CONTRACT
# ============================================================

def test_expired_contract():
    engine = OptionGreeksEngine()

    contract = make_contract(
        expiry="2026-08-19"
    )

    result = engine.enrich_contract(
        contract=contract,
        spot_price=25000,
        current_time=CURRENT_TIME,
    )

    assert_false(
        result["valid"],
        "Expired contract must be rejected",
    )

    assert_equal(
        result["reason"],
        "INVALID_GREEKS_INPUT",
        "Wrong expired-contract reason",
    )

    assert_true(
        "OPTION_EXPIRED"
        in result["errors"],
        "OPTION_EXPIRED error missing",
    )

    print("Reason :", result["reason"])
    print("Errors :", result["errors"])
    print("✅ PASS — Expired contract rejected")


# ============================================================
# TEST 15 — INVALID SPOT
# ============================================================

def test_invalid_spot():
    engine = OptionGreeksEngine()

    result = engine.enrich_contract(
        contract=make_contract(),
        spot_price=0,
        current_time=CURRENT_TIME,
    )

    assert_false(
        result["valid"],
        "Zero spot must reject Greeks calculation",
    )

    assert_true(
        "INVALID_SPOT_PRICE"
        in result["errors"],
        "Invalid spot error missing",
    )

    print("Errors :", result["errors"])
    print("✅ PASS — Invalid spot rejected")


# ============================================================
# TEST 16 — INVALID OPTION TYPE
# ============================================================

def test_invalid_option_type():
    engine = OptionGreeksEngine()

    result = engine.enrich_contract(
        contract=make_contract(
            option_type="XX"
        ),
        spot_price=25000,
        current_time=CURRENT_TIME,
    )

    assert_false(
        result["valid"],
        "Invalid option type must reject",
    )

    assert_true(
        "INVALID_OPTION_TYPE"
        in result["errors"],
        "Invalid option-type error missing",
    )

    print("Errors :", result["errors"])
    print("✅ PASS — Invalid option type rejected")


# ============================================================
# TEST 17 — ENRICH SINGLE CONTRACT
# ============================================================

def test_enrich_contract():
    engine = OptionGreeksEngine()

    contract = theoretical_contract(
        engine=engine,
        symbol="NIFTY26AUG25000CE",
        strike=25000,
        option_type="CE",
        volatility=0.17,
    )

    result = engine.enrich_contract(
        contract=contract,
        spot_price=25000,
        current_time=CURRENT_TIME,
    )

    assert_true(
        result["valid"],
        "Valid contract should receive Greeks",
    )

    enriched = result["contract"]

    for field in [
        "iv",
        "iv_decimal",
        "delta",
        "gamma",
        "theta",
        "vega",
    ]:
        assert_finite(
            enriched[field],
            f"{field} should be finite",
        )

    print("Symbol :", enriched["tradingsymbol"])
    print("IV %   :", enriched["iv"])
    print("Delta  :", enriched["delta"])
    print("Gamma  :", enriched["gamma"])
    print("Theta  :", enriched["theta"])
    print("Vega   :", enriched["vega"])
    print("✅ PASS — Contract enriched with IV + Greeks")


# ============================================================
# TEST 18 — COMPLETE CHAIN
# ============================================================

def test_complete_chain():
    engine = OptionGreeksEngine()

    contracts = [
        theoretical_contract(
            engine,
            "NIFTY26AUG24900CE",
            24900,
            "CE",
            0.16,
        ),
        theoretical_contract(
            engine,
            "NIFTY26AUG25000CE",
            25000,
            "CE",
            0.17,
        ),
        theoretical_contract(
            engine,
            "NIFTY26AUG25000PE",
            25000,
            "PE",
            0.17,
        ),
        theoretical_contract(
            engine,
            "NIFTY26AUG25100PE",
            25100,
            "PE",
            0.16,
        ),
    ]

    result = engine.enrich_option_chain(
        contracts=contracts,
        spot_price=25000,
        current_time=CURRENT_TIME,
    )

    assert_equal(
        result["greeks_permission"],
        "ALLOW",
        "Valid chain should be allowed",
    )

    assert_true(
        result["greeks_allowed"],
        "Valid Greeks chain should be allowed",
    )

    assert_equal(
        result["enriched_count"],
        4,
        "All contracts should enrich",
    )

    assert_equal(
        result["rejected_count"],
        0,
        "No contracts should reject",
    )

    print("Input Count    :", result["input_count"])
    print("Enriched Count :", result["enriched_count"])
    print("Rejected Count :", result["rejected_count"])
    print("✅ PASS — Complete chain enriched")


# ============================================================
# TEST 19 — BAD CONTRACT ISOLATION
# ============================================================

def test_bad_contract_isolation():
    engine = OptionGreeksEngine()

    good = theoretical_contract(
        engine,
        "NIFTY26AUG25000CE",
        25000,
        "CE",
        0.17,
    )

    bad = make_contract(
        symbol="NIFTY26AUG26000XX",
        strike=26000,
        option_type="XX",
    )

    result = engine.enrich_option_chain(
        contracts=[good, bad],
        spot_price=25000,
        current_time=CURRENT_TIME,
    )

    assert_equal(
        result["enriched_count"],
        1,
        "Good contract must survive bad neighbor",
    )

    assert_equal(
        result["rejected_count"],
        1,
        "Bad contract should reject independently",
    )

    assert_equal(
        result["greeks_permission"],
        "ALLOW",
        "One bad contract must not destroy valid chain",
    )

    print("Enriched :", result["enriched_count"])
    print("Rejected :", result["rejected_count"])
    print("✅ PASS — Bad contract isolated safely")


# ============================================================
# TEST 20 — ORIGINAL CONTRACT IMMUTABILITY
# ============================================================

def test_original_not_mutated():
    engine = OptionGreeksEngine()

    contract = theoretical_contract(
        engine,
        "NIFTY26AUG25000CE",
        25000,
        "CE",
        0.17,
    )

    original = deepcopy(
        contract
    )

    result = engine.enrich_contract(
        contract=contract,
        spot_price=25000,
        current_time=CURRENT_TIME,
    )

    assert_true(
        result["valid"],
        "Enrichment should succeed",
    )

    assert_equal(
        contract,
        original,
        "Greeks engine must not mutate adapter contract",
    )

    print("Original Mutated : NO")
    print("✅ PASS — Input market contract remains immutable")


# ============================================================
# TEST 21 — DETERMINISTIC ORDERING
# ============================================================

def test_deterministic_ordering():
    engine = OptionGreeksEngine()

    contracts = [
        theoretical_contract(
            engine,
            "NIFTY26AUG25100PE",
            25100,
            "PE",
            0.16,
        ),
        theoretical_contract(
            engine,
            "NIFTY26AUG24900PE",
            24900,
            "PE",
            0.16,
        ),
        theoretical_contract(
            engine,
            "NIFTY26AUG25000PE",
            25000,
            "PE",
            0.16,
        ),
    ]

    result = engine.enrich_option_chain(
        contracts=contracts,
        spot_price=25000,
        current_time=CURRENT_TIME,
    )

    symbols = [
        item["tradingsymbol"]
        for item in result["contracts"]
    ]

    expected = [
        "NIFTY26AUG24900PE",
        "NIFTY26AUG25000PE",
        "NIFTY26AUG25100PE",
    ]

    assert_equal(
        symbols,
        expected,
        "Greeks chain ordering must be deterministic",
    )

    print("Ordered Symbols:")
    for symbol in symbols:
        print(" ", symbol)

    print("✅ PASS — Deterministic ordering preserved")


# ============================================================
# TEST 22 — IV DECIMAL / PERCENT CONVENTION
# ============================================================

def test_iv_units():
    engine = OptionGreeksEngine()

    contract = theoretical_contract(
        engine,
        "NIFTY26AUG25000CE",
        25000,
        "CE",
        0.20,
    )

    result = engine.enrich_contract(
        contract=contract,
        spot_price=25000,
        current_time=CURRENT_TIME,
    )

    assert_true(
        result["valid"],
        "IV convention test contract should enrich",
    )

    enriched = result["contract"]

    assert_close(
        enriched["iv_decimal"],
        0.20,
        "Decimal IV convention incorrect",
        tolerance=0.0001,
    )

    assert_close(
        enriched["iv"],
        20.0,
        "Percentage IV convention incorrect",
        tolerance=0.01,
    )

    assert_close(
        enriched["iv"],
        enriched["iv_decimal"] * 100,
        "IV percentage/decimal relationship broken",
        tolerance=0.0001,
    )

    print("IV Decimal :", enriched["iv_decimal"])
    print("IV Percent :", enriched["iv"])
    print("✅ PASS — IV units explicitly separated")


# ============================================================
# TEST 23 — NEAR EXPIRY FINITE
# ============================================================

def test_near_expiry():
    engine = OptionGreeksEngine()

    current = datetime(
        2026,
        8,
        25,
        15,
        0,
        0,
    )

    t = engine.calculate_time_to_expiry(
        expiry="2026-08-25",
        current_time=current,
    )

    assert_true(
        t > 0,
        "30 minutes before expiry must have positive T",
    )

    price = engine.black_scholes_price(
        spot=25000,
        strike=25000,
        time_to_expiry=t,
        volatility=0.20,
        option_type="CE",
    )

    greeks = engine.calculate_greeks(
        spot=25000,
        strike=25000,
        time_to_expiry=t,
        volatility=0.20,
        option_type="CE",
    )

    assert_finite(
        price,
        "Near-expiry price must remain finite",
    )

    for name, value in greeks.items():
        assert_finite(
            value,
            f"Near-expiry {name} must remain finite",
        )

    print("Time Years :", t)
    print("Price      :", price)
    print("Delta      :", greeks["delta"])
    print("Gamma      :", greeks["gamma"])
    print("Theta      :", greeks["theta"])
    print("Vega       :", greeks["vega"])
    print("✅ PASS — Near-expiry mathematics remains finite")


# ============================================================
# TEST 24 — ZERO AUTHORITY
# ============================================================

def test_zero_authority():
    engine = OptionGreeksEngine()

    contract = theoretical_contract(
        engine,
        "NIFTY26AUG25000CE",
        25000,
        "CE",
        0.17,
    )

    result = engine.enrich_option_chain(
        contracts=[contract],
        spot_price=25000,
        current_time=CURRENT_TIME,
    )

    assert_false(
        result["broker_order_allowed"],
        "Greeks engine can never authorize broker orders",
    )

    forbidden_result_fields = [
        "final_lots",
        "final_quantity",
        "approved_risk",
        "authorized_risk",
        "final_authorized_risk_rupees",
        "daily_risk_budget_rupees",
        "remaining_daily_risk_rupees",
        "order_id",
        "transaction_type",
    ]

    forbidden_contract_fields = [
        "final_lots",
        "final_quantity",
        "approved_risk",
        "authorized_risk",
        "final_authorized_risk_rupees",
        "order_id",
        "transaction_type",
    ]

    for field in forbidden_result_fields:
        if field in result:
            raise AssertionError(
                "Greeks engine illegally contains authority: "
                f"{field}"
            )

    for enriched in result["contracts"]:
        for field in forbidden_contract_fields:
            if field in enriched:
                raise AssertionError(
                    "Greeks contract illegally contains authority: "
                    f"{field}"
                )

    print("Pricing Authority     : YES")
    print("Greeks Authority      : YES")
    print("Contract Selection    : NO")
    print("Trade Decision        : NO")
    print("Risk Allocation       : NO")
    print("Position Sizing       : NO")
    print("Broker Execution      : NO")
    print()
    print("✅ PASS — Greeks engine is analytical only")


# ============================================================
# TEST 25 — BLOCKED RESULT HAS ZERO AUTHORITY
# ============================================================

def test_blocked_zero_authority():
    engine = OptionGreeksEngine()

    result = engine.enrich_option_chain(
        contracts=[
            make_contract(
                option_type="INVALID"
            )
        ],
        spot_price=25000,
        current_time=CURRENT_TIME,
    )

    assert_equal(
        result["greeks_permission"],
        "BLOCK",
        "Invalid-only chain must block",
    )

    assert_false(
        result["greeks_allowed"],
        "Blocked Greeks result cannot be allowed",
    )

    assert_equal(
        result["contracts"],
        [],
        "Blocked result must expose no enriched contracts",
    )

    assert_false(
        result["broker_order_allowed"],
        "Blocked result cannot authorize broker execution",
    )

    assert_equal(
        result["reason"],
        "NO_VALID_GREEKS_CONTRACTS",
        "Wrong blocked-chain reason",
    )

    print("Permission           :", result["greeks_permission"])
    print("Greeks Allowed       :", result["greeks_allowed"])
    print("Contracts            :", result["contracts"])
    print("Broker Order Allowed :", result["broker_order_allowed"])
    print()
    print(
        "✅ PASS — Blocked Greeks chain cannot create "
        "downstream order authority"
    )


# ============================================================
# TEST RUNNER
# ============================================================

def run_all_tests():
    global PASSED
    global TOTAL

    tests = [
        ("TEST 1 — BLACK-SCHOLES CALL PRICE", test_call_price),
        ("TEST 2 — BLACK-SCHOLES PUT PRICE", test_put_price),
        ("TEST 3 — PUT-CALL PARITY", test_put_call_parity),
        ("TEST 4 — IV RECOVERY", test_iv_recovery),
        ("TEST 5 — CALL DELTA", test_call_delta),
        ("TEST 6 — PUT DELTA", test_put_delta),
        ("TEST 7 — GAMMA", test_gamma),
        ("TEST 8 — VEGA", test_vega),
        ("TEST 9 — THETA", test_theta),
        ("TEST 10 — DEEP ITM / OTM", test_deep_delta_behavior),
        ("TEST 11 — MIDPOINT PREFERRED", test_midpoint_preferred),
        ("TEST 12 — LTP FALLBACK", test_ltp_fallback),
        ("TEST 13 — IMPOSSIBLE PRICE", test_impossible_price),
        ("TEST 14 — EXPIRED CONTRACT", test_expired_contract),
        ("TEST 15 — INVALID SPOT", test_invalid_spot),
        ("TEST 16 — INVALID OPTION TYPE", test_invalid_option_type),
        ("TEST 17 — ENRICH CONTRACT", test_enrich_contract),
        ("TEST 18 — COMPLETE CHAIN", test_complete_chain),
        ("TEST 19 — BAD CONTRACT ISOLATION", test_bad_contract_isolation),
        ("TEST 20 — INPUT IMMUTABILITY", test_original_not_mutated),
        ("TEST 21 — DETERMINISTIC ORDERING", test_deterministic_ordering),
        ("TEST 22 — IV UNITS", test_iv_units),
        ("TEST 23 — NEAR EXPIRY", test_near_expiry),
        ("TEST 24 — ZERO AUTHORITY", test_zero_authority),
        ("TEST 25 — BLOCKED ZERO AUTHORITY", test_blocked_zero_authority),
    ]

    TOTAL = len(tests)

    heading(
        "THETA AI TRADER — OPTION GREEKS ENGINE TEST SUITE"
    )

    print()
    print("Live Zerodha Calls : NONE")
    print("Real Orders        : NONE")
    print("Broker Execution   : DISABLED")
    print("Pricing Model      : BLACK-SCHOLES")
    print("IV Solver          : BOUNDED BISECTION")

    for title, test in tests:
        heading(title)

        try:
            test()
            PASSED += 1

        except Exception as error:
            print()
            print("❌ TEST FAILED")
            print("Test :", test.__name__)
            print("Error:", error)
            print()

            line()

            print(
                f"❌ OPTION GREEKS ENGINE TESTS FAILED "
                f"({PASSED}/{TOTAL} passed)"
            )

            line()
            raise

    print()
    line()

    print(
        f"✅ ALL OPTION GREEKS ENGINE TESTS PASSED "
        f"({PASSED}/{TOTAL})"
    )

    print("🔒 GREEKS ENGINE IS ANALYTICAL ONLY")
    print("🔒 NO CONTRACT-SELECTION AUTHORITY")
    print("🔒 NO TRADE-DECISION AUTHORITY")
    print("🔒 NO RISK/POSITION-SIZING AUTHORITY")
    print("🔒 NO BROKER ORDER AUTHORITY")

    line()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    run_all_tests()