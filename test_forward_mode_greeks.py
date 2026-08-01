# ============================================================
# THETA AI TRADER
# FORWARD-MODE OPTION GREEKS — TEST SUITE
# ============================================================

import math
from copy import deepcopy
from datetime import datetime, timedelta

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
    if actual is None:
        raise AssertionError(
            f"{message}\nExpected: {expected}\nActual  : None"
        )

    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}\n"
            f"Tolerance: {tolerance}"
        )


def assert_finite(value, message):
    if value is None:
        raise AssertionError(
            f"{message}\nActual: None"
        )

    if not math.isfinite(float(value)):
        raise AssertionError(
            f"{message}\nActual: {value}"
        )


# ============================================================
# TEST DATA HELPERS
# ============================================================

def make_contract(
    strike,
    option_type,
    price,
    expiry,
    bid=None,
    ask=None,
    symbol=None,
):
    strike = float(strike)
    price = float(price)

    if symbol is None:
        symbol = (
            f"NIFTY_TEST_{int(strike)}_{option_type}"
        )

    if bid is None:
        bid = max(
            0.05,
            price - 0.05,
        )

    if ask is None:
        ask = price + 0.05

    return {
        "underlying": "NIFTY",
        "exchange": "NFO",
        "tradingsymbol": symbol,
        "expiry": expiry,
        "strike": strike,
        "option_type": option_type,
        "ltp": price,
        "bid": bid,
        "ask": ask,
        "oi": 100000,
        "volume": 50000,
    }


def make_expiry(
    current_time,
    days=7,
):
    return current_time + timedelta(
        days=days
    )


def make_black76_contract(
    engine,
    forward,
    strike,
    volatility,
    option_type,
    current_time,
    days=7,
):
    expiry = make_expiry(
        current_time,
        days=days,
    )

    t = engine.calculate_time_to_expiry(
        expiry=expiry,
        current_time=current_time,
    )

    price = engine.black76_price(
        forward=forward,
        strike=strike,
        time_to_expiry=t,
        volatility=volatility,
        option_type=option_type,
    )

    return make_contract(
        strike=strike,
        option_type=option_type,
        price=price,
        expiry=expiry,
        bid=price,
        ask=price,
    )


# ============================================================
# TEST 1 — BLACK-76 CALL PRICE
# ============================================================

def test_black76_call_price():
    engine = OptionGreeksEngine(
        risk_free_rate=0.06
    )

    forward = 24400.0
    strike = 24400.0
    t = 7 / 365
    volatility = 0.15

    price = engine.black76_price(
        forward=forward,
        strike=strike,
        time_to_expiry=t,
        volatility=volatility,
        option_type="CE",
    )

    assert_finite(
        price,
        "Black-76 CE price must be finite",
    )

    assert_true(
        price > 0,
        "Black-76 CE price must be positive",
    )

    print("Forward :", forward)
    print("Strike  :", strike)
    print("IV      :", volatility)
    print("CE Price:", price)
    print("✅ PASS — Black-76 call pricing works")


# ============================================================
# TEST 2 — BLACK-76 PUT PRICE
# ============================================================

def test_black76_put_price():
    engine = OptionGreeksEngine(
        risk_free_rate=0.06
    )

    forward = 24400.0
    strike = 24400.0
    t = 7 / 365
    volatility = 0.15

    price = engine.black76_price(
        forward=forward,
        strike=strike,
        time_to_expiry=t,
        volatility=volatility,
        option_type="PE",
    )

    assert_finite(
        price,
        "Black-76 PE price must be finite",
    )

    assert_true(
        price > 0,
        "Black-76 PE price must be positive",
    )

    print("Forward :", forward)
    print("Strike  :", strike)
    print("IV      :", volatility)
    print("PE Price:", price)
    print("✅ PASS — Black-76 put pricing works")


# ============================================================
# TEST 3 — BLACK-76 PUT-CALL PARITY
# ============================================================

def test_black76_put_call_parity():
    engine = OptionGreeksEngine(
        risk_free_rate=0.06
    )

    forward = 24425.0
    strike = 24400.0
    t = 7 / 365
    volatility = 0.17

    call = engine.black76_price(
        forward=forward,
        strike=strike,
        time_to_expiry=t,
        volatility=volatility,
        option_type="CE",
    )

    put = engine.black76_price(
        forward=forward,
        strike=strike,
        time_to_expiry=t,
        volatility=volatility,
        option_type="PE",
    )

    expected_difference = (
        math.exp(
            -engine.risk_free_rate * t
        )
        * (
            forward - strike
        )
    )

    actual_difference = (
        call - put
    )

    assert_close(
        actual_difference,
        expected_difference,
        "Black-76 put-call parity failed",
        tolerance=0.000001,
    )

    print("Call - Put :", actual_difference)
    print("Parity RHS :", expected_difference)
    print("✅ PASS — Black-76 put-call parity holds")


# ============================================================
# TEST 4 — CALL IV RECOVERY
# ============================================================

def test_call_iv_recovery():
    engine = OptionGreeksEngine()

    forward = 24420.0
    strike = 24400.0
    t = 5 / 365
    expected_iv = 0.16

    market_price = engine.black76_price(
        forward=forward,
        strike=strike,
        time_to_expiry=t,
        volatility=expected_iv,
        option_type="CE",
    )

    recovered_iv = (
        engine.calculate_forward_implied_volatility(
            market_price=market_price,
            forward=forward,
            strike=strike,
            time_to_expiry=t,
            option_type="CE",
        )
    )

    assert_close(
        recovered_iv,
        expected_iv,
        "Forward CE IV recovery failed",
        tolerance=0.00001,
    )

    print("Expected IV :", expected_iv)
    print("Recovered IV:", recovered_iv)
    print("✅ PASS — Forward CE IV recovered")


# ============================================================
# TEST 5 — PUT IV RECOVERY
# ============================================================

def test_put_iv_recovery():
    engine = OptionGreeksEngine()

    forward = 24380.0
    strike = 24400.0
    t = 5 / 365
    expected_iv = 0.18

    market_price = engine.black76_price(
        forward=forward,
        strike=strike,
        time_to_expiry=t,
        volatility=expected_iv,
        option_type="PE",
    )

    recovered_iv = (
        engine.calculate_forward_implied_volatility(
            market_price=market_price,
            forward=forward,
            strike=strike,
            time_to_expiry=t,
            option_type="PE",
        )
    )

    assert_close(
        recovered_iv,
        expected_iv,
        "Forward PE IV recovery failed",
        tolerance=0.00001,
    )

    print("Expected IV :", expected_iv)
    print("Recovered IV:", recovered_iv)
    print("✅ PASS — Forward PE IV recovered")


# ============================================================
# TEST 6 — SAME STRIKE CE/PE IV CONSISTENCY
# ============================================================

def test_ce_pe_iv_consistency():
    engine = OptionGreeksEngine()

    forward = 24390.0
    strike = 24400.0
    t = 5 / 365
    expected_iv = 0.175

    call_price = engine.black76_price(
        forward=forward,
        strike=strike,
        time_to_expiry=t,
        volatility=expected_iv,
        option_type="CE",
    )

    put_price = engine.black76_price(
        forward=forward,
        strike=strike,
        time_to_expiry=t,
        volatility=expected_iv,
        option_type="PE",
    )

    call_iv = (
        engine.calculate_forward_implied_volatility(
            market_price=call_price,
            forward=forward,
            strike=strike,
            time_to_expiry=t,
            option_type="CE",
        )
    )

    put_iv = (
        engine.calculate_forward_implied_volatility(
            market_price=put_price,
            forward=forward,
            strike=strike,
            time_to_expiry=t,
            option_type="PE",
        )
    )

    assert_close(
        call_iv,
        put_iv,
        "Parity-consistent CE/PE must recover same IV",
        tolerance=0.00001,
    )

    assert_close(
        call_iv,
        expected_iv,
        "Recovered common IV incorrect",
        tolerance=0.00001,
    )

    print("CE IV :", call_iv)
    print("PE IV :", put_iv)
    print("✅ PASS — Same-strike CE/PE IV is consistent")


# ============================================================
# TEST 7 — FORWARD GREEKS FINITE
# ============================================================

def test_forward_greeks_finite():
    engine = OptionGreeksEngine()

    greeks = engine.calculate_forward_greeks(
        forward=24400,
        strike=24400,
        time_to_expiry=5 / 365,
        volatility=0.15,
        option_type="CE",
    )

    assert_true(
        greeks is not None,
        "Forward Greeks should calculate",
    )

    for key in (
        "delta",
        "gamma",
        "theta",
        "vega",
    ):
        assert_finite(
            greeks[key],
            f"{key} must be finite",
        )

    assert_equal(
        greeks["delta_basis"],
        "FORWARD",
        "Forward delta must be explicitly labelled",
    )

    assert_equal(
        greeks["gamma_basis"],
        "FORWARD",
        "Forward gamma must be explicitly labelled",
    )

    print("Delta :", greeks["delta"])
    print("Gamma :", greeks["gamma"])
    print("Theta :", greeks["theta"])
    print("Vega  :", greeks["vega"])
    print("✅ PASS — Forward Greeks are finite")


# ============================================================
# TEST 8 — CALL DELTA RANGE
# ============================================================

def test_call_delta_range():
    engine = OptionGreeksEngine()

    greeks = engine.calculate_forward_greeks(
        forward=24400,
        strike=24400,
        time_to_expiry=5 / 365,
        volatility=0.15,
        option_type="CE",
    )

    assert_true(
        0 < greeks["delta"] < 1,
        "Forward call delta must lie between 0 and 1",
    )

    print("Call Delta :", greeks["delta"])
    print("✅ PASS — Forward call delta range valid")


# ============================================================
# TEST 9 — PUT DELTA RANGE
# ============================================================

def test_put_delta_range():
    engine = OptionGreeksEngine()

    greeks = engine.calculate_forward_greeks(
        forward=24400,
        strike=24400,
        time_to_expiry=5 / 365,
        volatility=0.15,
        option_type="PE",
    )

    assert_true(
        -1 < greeks["delta"] < 0,
        "Forward put delta must lie between -1 and 0",
    )

    print("Put Delta :", greeks["delta"])
    print("✅ PASS — Forward put delta range valid")


# ============================================================
# TEST 10 — GAMMA POSITIVE
# ============================================================

def test_gamma_positive():
    engine = OptionGreeksEngine()

    ce = engine.calculate_forward_greeks(
        forward=24400,
        strike=24400,
        time_to_expiry=5 / 365,
        volatility=0.15,
        option_type="CE",
    )

    pe = engine.calculate_forward_greeks(
        forward=24400,
        strike=24400,
        time_to_expiry=5 / 365,
        volatility=0.15,
        option_type="PE",
    )

    assert_true(
        ce["gamma"] > 0,
        "CE gamma must be positive",
    )

    assert_true(
        pe["gamma"] > 0,
        "PE gamma must be positive",
    )

    assert_close(
        ce["gamma"],
        pe["gamma"],
        "Same-strike CE/PE forward gamma should match",
        tolerance=0.0000001,
    )

    print("CE Gamma :", ce["gamma"])
    print("PE Gamma :", pe["gamma"])
    print("✅ PASS — Forward gamma is positive and symmetric")


# ============================================================
# TEST 11 — VEGA POSITIVE
# ============================================================

def test_vega_positive():
    engine = OptionGreeksEngine()

    greeks = engine.calculate_forward_greeks(
        forward=24400,
        strike=24400,
        time_to_expiry=5 / 365,
        volatility=0.15,
        option_type="CE",
    )

    assert_true(
        greeks["vega"] > 0,
        "Forward vega must be positive",
    )

    print("Vega :", greeks["vega"])
    print("✅ PASS — Forward vega positive")


# ============================================================
# TEST 12 — FORWARD MODE ENRICH CONTRACT
# ============================================================

def test_forward_enrich_contract():
    engine = OptionGreeksEngine()

    current_time = datetime(
        2026, 8, 1, 10, 0, 0
    )

    forward = 24400.0
    volatility = 0.15

    contract = make_black76_contract(
        engine=engine,
        forward=forward,
        strike=24400,
        volatility=volatility,
        option_type="CE",
        current_time=current_time,
    )

    result = engine.enrich_contract(
        contract=contract,
        spot_price=24383.60,
        current_time=current_time,
        pricing_mode="FORWARD",
        implied_forward=forward,
    )

    assert_true(
        result["valid"],
        "Valid FORWARD contract must enrich",
    )

    enriched = result["contract"]

    assert_equal(
        enriched["pricing_mode"],
        "FORWARD",
        "Pricing mode metadata incorrect",
    )

    assert_close(
        enriched["implied_forward"],
        forward,
        "Implied-forward metadata incorrect",
    )

    assert_close(
        enriched["iv_decimal"],
        volatility,
        "Enriched forward IV incorrect",
        tolerance=0.00001,
    )

    assert_equal(
        enriched["delta_basis"],
        "FORWARD",
        "Delta basis metadata incorrect",
    )

    print("Pricing Mode :", enriched["pricing_mode"])
    print("Forward      :", enriched["implied_forward"])
    print("IV %         :", enriched["iv"])
    print("Delta        :", enriched["delta"])
    print("✅ PASS — Contract enriched in explicit FORWARD mode")


# ============================================================
# TEST 13 — MISSING FORWARD BLOCKS
# ============================================================

def test_missing_forward_blocks():
    engine = OptionGreeksEngine()

    current_time = datetime(
        2026, 8, 1, 10, 0, 0
    )

    contract = make_black76_contract(
        engine=engine,
        forward=24400,
        strike=24400,
        volatility=0.15,
        option_type="CE",
        current_time=current_time,
    )

    result = engine.enrich_contract(
        contract=contract,
        spot_price=24383.60,
        current_time=current_time,
        pricing_mode="FORWARD",
        implied_forward=None,
    )

    assert_false(
        result["valid"],
        "FORWARD mode without forward must block",
    )

    assert_true(
        "INVALID_IMPLIED_FORWARD"
        in result["errors"],
        "Missing-forward error not reported",
    )

    print("Reason :", result["reason"])
    print("Errors :", result["errors"])
    print("✅ PASS — Missing forward cannot silently fall back")


# ============================================================
# TEST 14 — INVALID FORWARD BLOCKS
# ============================================================

def test_invalid_forward_blocks():
    engine = OptionGreeksEngine()

    current_time = datetime(
        2026, 8, 1, 10, 0, 0
    )

    contract = make_black76_contract(
        engine=engine,
        forward=24400,
        strike=24400,
        volatility=0.15,
        option_type="PE",
        current_time=current_time,
    )

    result = engine.enrich_contract(
        contract=contract,
        spot_price=24383.60,
        current_time=current_time,
        pricing_mode="FORWARD",
        implied_forward=0,
    )

    assert_false(
        result["valid"],
        "Zero forward must block",
    )

    assert_true(
        "INVALID_IMPLIED_FORWARD"
        in result["errors"],
        "Invalid-forward error missing",
    )

    print("Errors :", result["errors"])
    print("✅ PASS — Invalid forward rejected")


# ============================================================
# TEST 15 — INVALID PRICING MODE
# ============================================================

def test_invalid_pricing_mode():
    engine = OptionGreeksEngine()

    result = engine.enrich_contract(
        contract={},
        spot_price=24383.60,
        pricing_mode="MAGIC",
        implied_forward=24400,
    )

    assert_false(
        result["valid"],
        "Unknown pricing mode must reject",
    )

    assert_equal(
        result["reason"],
        "INVALID_PRICING_MODE",
        "Wrong pricing-mode rejection",
    )

    print("Reason :", result["reason"])
    print("✅ PASS — Unknown pricing mode rejected")


# ============================================================
# TEST 16 — CHAIN FORWARD MODE
# ============================================================

def test_forward_chain():
    engine = OptionGreeksEngine()

    current_time = datetime(
        2026, 8, 1, 10, 0, 0
    )

    forward = 24390.0
    volatility = 0.16

    contracts = []

    for strike in (
        24300,
        24350,
        24400,
        24450,
        24500,
    ):
        for option_type in (
            "CE",
            "PE",
        ):
            contracts.append(
                make_black76_contract(
                    engine=engine,
                    forward=forward,
                    strike=strike,
                    volatility=volatility,
                    option_type=option_type,
                    current_time=current_time,
                )
            )

    result = engine.enrich_option_chain(
        contracts=contracts,
        spot_price=24383.60,
        current_time=current_time,
        pricing_mode="FORWARD",
        implied_forward=forward,
    )

    assert_true(
        result["greeks_allowed"],
        "Valid forward chain should enrich",
    )

    assert_equal(
        result["enriched_count"],
        10,
        "All ten synthetic contracts should enrich",
    )

    assert_equal(
        result["rejected_count"],
        0,
        "Synthetic parity-consistent chain should have no rejection",
    )

    assert_equal(
        result["pricing_mode"],
        "FORWARD",
        "Chain pricing mode incorrect",
    )

    print("Input Count    :", result["input_count"])
    print("Enriched Count :", result["enriched_count"])
    print("Rejected Count :", result["rejected_count"])
    print("✅ PASS — Full chain enriched in FORWARD mode")


# ============================================================
# TEST 17 — CHAIN MISSING FORWARD BLOCKS
# ============================================================

def test_chain_missing_forward():
    engine = OptionGreeksEngine()

    result = engine.enrich_option_chain(
        contracts=[],
        spot_price=24383.60,
        pricing_mode="FORWARD",
        implied_forward=None,
    )

    assert_false(
        result["greeks_allowed"],
        "FORWARD chain without forward must block",
    )

    assert_equal(
        result["reason"],
        "IMPLIED_FORWARD_REQUIRED",
        "Wrong missing-forward chain reason",
    )

    assert_equal(
        result["pricing_mode"],
        "FORWARD",
        "Blocked result must retain requested mode",
    )

    print("Permission :", result["greeks_permission"])
    print("Reason     :", result["reason"])
    print("✅ PASS — Chain cannot silently fall back to SPOT")


# ============================================================
# TEST 18 — INPUT IMMUTABILITY
# ============================================================

def test_input_immutability():
    engine = OptionGreeksEngine()

    current_time = datetime(
        2026, 8, 1, 10, 0, 0
    )

    forward = 24400.0

    contract = make_black76_contract(
        engine=engine,
        forward=forward,
        strike=24400,
        volatility=0.15,
        option_type="CE",
        current_time=current_time,
    )

    original = deepcopy(
        contract
    )

    result = engine.enrich_contract(
        contract=contract,
        spot_price=24383.60,
        current_time=current_time,
        pricing_mode="FORWARD",
        implied_forward=forward,
    )

    assert_true(
        result["valid"],
        "Synthetic contract should enrich",
    )

    assert_equal(
        contract,
        original,
        "Greeks engine must not mutate input contract",
    )

    print("Input Mutated : NO")
    print("✅ PASS — Forward enrichment preserves input immutability")


# ============================================================
# TEST 19 — DETERMINISTIC CHAIN
# ============================================================

def test_deterministic_chain():
    engine = OptionGreeksEngine()

    current_time = datetime(
        2026, 8, 1, 10, 0, 0
    )

    forward = 24400.0

    contracts = []

    for strike in (
        24350,
        24400,
        24450,
    ):
        for option_type in (
            "CE",
            "PE",
        ):
            contracts.append(
                make_black76_contract(
                    engine=engine,
                    forward=forward,
                    strike=strike,
                    volatility=0.15,
                    option_type=option_type,
                    current_time=current_time,
                )
            )

    first = engine.enrich_option_chain(
        contracts=contracts,
        spot_price=24383.60,
        current_time=current_time,
        pricing_mode="FORWARD",
        implied_forward=forward,
    )

    second = engine.enrich_option_chain(
        contracts=list(
            reversed(contracts)
        ),
        spot_price=24383.60,
        current_time=current_time,
        pricing_mode="FORWARD",
        implied_forward=forward,
    )

    first_symbols = [
        item["tradingsymbol"]
        for item in first["contracts"]
    ]

    second_symbols = [
        item["tradingsymbol"]
        for item in second["contracts"]
    ]

    assert_equal(
        first_symbols,
        second_symbols,
        "Input ordering changed deterministic chain output",
    )

    print("Contract Order Stable : YES")
    print("✅ PASS — Forward chain enrichment deterministic")


# ============================================================
# TEST 20 — DEEP ITM CALL IS SOLVABLE
# ============================================================

def test_deep_itm_call_solvable():
    """
    Regression protection for the class of ITM calls that
    previously failed when spot-based intrinsic-value bounds
    conflicted with the option market.
    """

    engine = OptionGreeksEngine()

    forward = 24350.0
    strike = 23900.0
    t = 3 / 365
    expected_iv = 0.14

    market_price = engine.black76_price(
        forward=forward,
        strike=strike,
        time_to_expiry=t,
        volatility=expected_iv,
        option_type="CE",
    )

    recovered_iv = (
        engine.calculate_forward_implied_volatility(
            market_price=market_price,
            forward=forward,
            strike=strike,
            time_to_expiry=t,
            option_type="CE",
        )
    )

    assert_close(
        recovered_iv,
        expected_iv,
        "Deep ITM forward call IV should be solvable",
        tolerance=0.00001,
    )

    print("Forward      :", forward)
    print("Strike       :", strike)
    print("Market Price :", market_price)
    print("Recovered IV :", recovered_iv)
    print("✅ PASS — Deep ITM call solvable under forward model")


# ============================================================
# TEST 21 — SIX ITM CALL REGRESSION FAMILY
# ============================================================

def test_six_itm_calls():
    """
    Tests the same strike family that was rejected in the
    earlier live pipeline:

        23900
        23950
        24000
        24050
        24100
        24150

    Synthetic Black-76-consistent prices are used here.
    This proves model solvability; it does NOT claim that
    historical live prices must necessarily pass.
    """

    engine = OptionGreeksEngine()

    current_time = datetime(
        2026, 8, 1, 10, 0, 0
    )

    forward = 24350.0
    volatility = 0.14

    strikes = (
        23900,
        23950,
        24000,
        24050,
        24100,
        24150,
    )

    contracts = []

    for strike in strikes:
        contracts.append(
            make_black76_contract(
                engine=engine,
                forward=forward,
                strike=strike,
                volatility=volatility,
                option_type="CE",
                current_time=current_time,
                days=3,
            )
        )

    result = engine.enrich_option_chain(
        contracts=contracts,
        spot_price=24383.60,
        current_time=current_time,
        pricing_mode="FORWARD",
        implied_forward=forward,
    )

    assert_true(
        result["greeks_allowed"],
        "ITM call regression family should enrich",
    )

    assert_equal(
        result["enriched_count"],
        6,
        "All six ITM calls should be solvable",
    )

    assert_equal(
        result["rejected_count"],
        0,
        "No synthetic ITM call should reject",
    )

    print("Strikes        :", strikes)
    print("Enriched Count :", result["enriched_count"])
    print("Rejected Count :", result["rejected_count"])
    print("✅ PASS — Six prior-failure strike classes are model-solvable")


# ============================================================
# TEST 22 — IMPOSSIBLE FORWARD PRICE REJECTED
# ============================================================

def test_impossible_forward_price():
    engine = OptionGreeksEngine()

    iv = engine.calculate_forward_implied_volatility(
        market_price=1.0,
        forward=25000,
        strike=24000,
        time_to_expiry=5 / 365,
        option_type="CE",
    )

    assert_equal(
        iv,
        None,
        "Call below discounted intrinsic value must reject",
    )

    print("Recovered IV :", iv)
    print("✅ PASS — Impossible forward-market price rejected")


# ============================================================
# TEST 23 — SPOT MODE STILL DEFAULT
# ============================================================

def test_spot_mode_default():
    engine = OptionGreeksEngine()

    current_time = datetime(
        2026, 8, 1, 10, 0, 0
    )

    spot = 24400.0
    strike = 24400.0
    volatility = 0.15

    expiry = make_expiry(
        current_time,
        days=7,
    )

    t = engine.calculate_time_to_expiry(
        expiry=expiry,
        current_time=current_time,
    )

    price = engine.black_scholes_price(
        spot=spot,
        strike=strike,
        time_to_expiry=t,
        volatility=volatility,
        option_type="CE",
    )

    contract = make_contract(
        strike=strike,
        option_type="CE",
        price=price,
        expiry=expiry,
        bid=price,
        ask=price,
    )

    result = engine.enrich_contract(
        contract=contract,
        spot_price=spot,
        current_time=current_time,
    )

    assert_true(
        result["valid"],
        "Default SPOT mode must remain functional",
    )

    assert_equal(
        result["contract"]["pricing_mode"],
        "SPOT",
        "Default mode must remain SPOT",
    )

    assert_equal(
        result["contract"]["delta_basis"],
        "SPOT",
        "Default delta basis must remain SPOT",
    )

    print("Pricing Mode :", result["contract"]["pricing_mode"])
    print("✅ PASS — Backward-compatible SPOT default preserved")


# ============================================================
# TEST 24 — FORWARD MODE ZERO AUTHORITY
# ============================================================

def test_forward_zero_authority():
    engine = OptionGreeksEngine()

    current_time = datetime(
        2026, 8, 1, 10, 0, 0
    )

    forward = 24400.0

    contract = make_black76_contract(
        engine=engine,
        forward=forward,
        strike=24400,
        volatility=0.15,
        option_type="CE",
        current_time=current_time,
    )

    result = engine.enrich_contract(
        contract=contract,
        spot_price=24383.60,
        current_time=current_time,
        pricing_mode="FORWARD",
        implied_forward=forward,
    )

    assert_true(
        result["valid"],
        "Valid analytical result expected",
    )

    assert_false(
        result["broker_order_allowed"],
        "Greeks engine cannot authorize broker order",
    )

    forbidden_fields = (
        "selected_contract",
        "selected_strike",
        "trade_signal",
        "trade_decision",
        "approved_risk",
        "authorized_risk",
        "final_lots",
        "final_quantity",
        "order_id",
        "transaction_type",
    )

    enriched = result["contract"]

    for field in forbidden_fields:
        if field in result:
            raise AssertionError(
                f"Illegal authority in result: {field}"
            )

        if field in enriched:
            raise AssertionError(
                f"Illegal authority in enriched contract: {field}"
            )

    print("Forward Analytics  : YES")
    print("Contract Selection : NO")
    print("Trade Decision     : NO")
    print("Risk Allocation    : NO")
    print("Position Sizing    : NO")
    print("Broker Execution   : NO")
    print("✅ PASS — FORWARD mode remains analytical only")


# ============================================================
# TEST 25 — BLOCKED FORWARD HAS ZERO AUTHORITY
# ============================================================

def test_blocked_forward_zero_authority():
    engine = OptionGreeksEngine()

    result = engine.enrich_option_chain(
        contracts=[],
        spot_price=24383.60,
        pricing_mode="FORWARD",
        implied_forward=None,
    )

    assert_equal(
        result["greeks_permission"],
        "BLOCK",
        "Missing forward must block chain",
    )

    assert_false(
        result["greeks_allowed"],
        "Blocked chain cannot allow Greeks processing",
    )

    assert_false(
        result["broker_order_allowed"],
        "Blocked result cannot authorize broker execution",
    )

    assert_equal(
        result["contracts"],
        [],
        "Blocked chain must produce no enriched contracts",
    )

    print("Permission           :", result["greeks_permission"])
    print("Greeks Allowed       :", result["greeks_allowed"])
    print("Broker Order Allowed :", result["broker_order_allowed"])
    print("✅ PASS — Blocked FORWARD mode has zero downstream authority")


# ============================================================
# TEST RUNNER
# ============================================================

def run_all_tests():
    global PASSED
    global TOTAL

    tests = [
        (
            "TEST 1 — BLACK-76 CALL PRICE",
            test_black76_call_price,
        ),
        (
            "TEST 2 — BLACK-76 PUT PRICE",
            test_black76_put_price,
        ),
        (
            "TEST 3 — BLACK-76 PUT-CALL PARITY",
            test_black76_put_call_parity,
        ),
        (
            "TEST 4 — CALL IV RECOVERY",
            test_call_iv_recovery,
        ),
        (
            "TEST 5 — PUT IV RECOVERY",
            test_put_iv_recovery,
        ),
        (
            "TEST 6 — CE/PE IV CONSISTENCY",
            test_ce_pe_iv_consistency,
        ),
        (
            "TEST 7 — FORWARD GREEKS FINITE",
            test_forward_greeks_finite,
        ),
        (
            "TEST 8 — CALL DELTA RANGE",
            test_call_delta_range,
        ),
        (
            "TEST 9 — PUT DELTA RANGE",
            test_put_delta_range,
        ),
        (
            "TEST 10 — GAMMA POSITIVE",
            test_gamma_positive,
        ),
        (
            "TEST 11 — VEGA POSITIVE",
            test_vega_positive,
        ),
        (
            "TEST 12 — FORWARD ENRICH CONTRACT",
            test_forward_enrich_contract,
        ),
        (
            "TEST 13 — MISSING FORWARD BLOCKS",
            test_missing_forward_blocks,
        ),
        (
            "TEST 14 — INVALID FORWARD BLOCKS",
            test_invalid_forward_blocks,
        ),
        (
            "TEST 15 — INVALID PRICING MODE",
            test_invalid_pricing_mode,
        ),
        (
            "TEST 16 — FORWARD CHAIN",
            test_forward_chain,
        ),
        (
            "TEST 17 — CHAIN MISSING FORWARD",
            test_chain_missing_forward,
        ),
        (
            "TEST 18 — INPUT IMMUTABILITY",
            test_input_immutability,
        ),
        (
            "TEST 19 — DETERMINISTIC CHAIN",
            test_deterministic_chain,
        ),
        (
            "TEST 20 — DEEP ITM CALL SOLVABLE",
            test_deep_itm_call_solvable,
        ),
        (
            "TEST 21 — SIX ITM CALL REGRESSION FAMILY",
            test_six_itm_calls,
        ),
        (
            "TEST 22 — IMPOSSIBLE FORWARD PRICE",
            test_impossible_forward_price,
        ),
        (
            "TEST 23 — SPOT MODE STILL DEFAULT",
            test_spot_mode_default,
        ),
        (
            "TEST 24 — FORWARD MODE ZERO AUTHORITY",
            test_forward_zero_authority,
        ),
        (
            "TEST 25 — BLOCKED FORWARD ZERO AUTHORITY",
            test_blocked_forward_zero_authority,
        ),
    ]

    TOTAL = len(tests)

    heading(
        "THETA AI TRADER — FORWARD-MODE OPTION GREEKS TEST SUITE"
    )

    print()
    print("Live Zerodha Calls : NONE")
    print("Real Orders        : NONE")
    print("Broker Execution   : DISABLED")
    print("Pricing Model      : BLACK-76")
    print("Forward Fallback   : DISABLED")

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
                "❌ FORWARD-MODE GREEKS TESTS FAILED "
                f"({PASSED}/{TOTAL} passed)"
            )

            line()

            raise

    print()
    line()

    print(
        "✅ ALL FORWARD-MODE GREEKS TESTS PASSED "
        f"({PASSED}/{TOTAL})"
    )

    print(
        "🔒 FORWARD MODE REQUIRES EXPLICIT IMPLIED FORWARD"
    )

    print(
        "🔒 NO SILENT FALLBACK TO SPOT PRICING"
    )

    print(
        "🔒 GREEKS ENGINE REMAINS ANALYTICAL ONLY"
    )

    print(
        "🔒 NO CONTRACT-SELECTION AUTHORITY"
    )

    print(
        "🔒 NO TRADE-DECISION AUTHORITY"
    )

    print(
        "🔒 NO RISK/POSITION-SIZING AUTHORITY"
    )

    print(
        "🔒 NO BROKER ORDER AUTHORITY"
    )

    line()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    run_all_tests()