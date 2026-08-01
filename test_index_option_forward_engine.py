# ============================================================
# THETA AI TRADER
# INDEX OPTION FORWARD ENGINE — TEST SUITE
# ============================================================

import math
from copy import deepcopy

from index_option_forward_engine import IndexOptionForwardEngine


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
            price - 0.10,
        )

    if ask is None:
        ask = price + 0.10

    return {
        "underlying": "NIFTY",
        "exchange": "NFO",
        "tradingsymbol": symbol,
        "strike": strike,
        "option_type": option_type,
        "ltp": price,
        "bid": bid,
        "ask": ask,
        "open_interest": 100000,
        "volume": 50000,
    }


def parity_prices(
    strike,
    forward,
    time_to_expiry,
    risk_free_rate=0.06,
    base_option_value=100.0,
):
    """
    Create CE/PE prices that exactly imply the requested forward.

    From:

        F = K + exp(rT) * (C - P)

    Therefore:

        C - P = exp(-rT) * (F - K)
    """

    discounted_difference = (
        math.exp(
            -risk_free_rate
            * time_to_expiry
        )
        * (
            forward
            - strike
        )
    )

    if discounted_difference >= 0:
        put_price = float(
            base_option_value
        )

        call_price = (
            put_price
            + discounted_difference
        )

    else:
        call_price = float(
            base_option_value
        )

        put_price = (
            call_price
            - discounted_difference
        )

    return (
        call_price,
        put_price,
    )


def make_pair(
    strike,
    forward,
    time_to_expiry,
    risk_free_rate=0.06,
    base_option_value=100.0,
):
    call_price, put_price = parity_prices(
        strike=strike,
        forward=forward,
        time_to_expiry=time_to_expiry,
        risk_free_rate=risk_free_rate,
        base_option_value=base_option_value,
    )

    return [
        make_contract(
            strike=strike,
            option_type="CE",
            price=call_price,
        ),
        make_contract(
            strike=strike,
            option_type="PE",
            price=put_price,
        ),
    ]


def make_chain(
    strikes,
    forward,
    time_to_expiry,
    risk_free_rate=0.06,
):
    contracts = []

    for strike in strikes:
        contracts.extend(
            make_pair(
                strike=strike,
                forward=forward,
                time_to_expiry=time_to_expiry,
                risk_free_rate=risk_free_rate,
            )
        )

    return contracts


# ============================================================
# TEST 1 — SINGLE STRIKE FORWARD RECOVERY
# ============================================================

def test_single_strike_forward_recovery():
    engine = IndexOptionForwardEngine(
        risk_free_rate=0.06
    )

    t = 7 / 365
    expected_forward = 24420.0
    strike = 24400.0

    call_price, put_price = parity_prices(
        strike=strike,
        forward=expected_forward,
        time_to_expiry=t,
    )

    forward = engine.estimate_forward_at_strike(
        strike=strike,
        call_price=call_price,
        put_price=put_price,
        time_to_expiry=t,
    )

    assert_close(
        forward,
        expected_forward,
        "Single-strike parity forward recovery failed",
        tolerance=0.000001,
    )

    print("Expected Forward :", expected_forward)
    print("Recovered Forward:", forward)
    print("✅ PASS — Put-call parity forward recovered exactly")


# ============================================================
# TEST 2 — NEGATIVE BASIS FORWARD
# ============================================================

def test_negative_basis():
    engine = IndexOptionForwardEngine()

    t = 5 / 365
    spot = 24383.60
    expected_forward = 24368.50

    contracts = make_chain(
        strikes=[
            24300,
            24350,
            24400,
        ],
        forward=expected_forward,
        time_to_expiry=t,
    )

    result = engine.estimate_forward(
        contracts=contracts,
        spot_price=spot,
        time_to_expiry=t,
    )

    assert_true(
        result["forward_allowed"],
        "Negative basis must be permitted when reasonable",
    )

    assert_close(
        result["implied_forward"],
        expected_forward,
        "Negative-basis implied forward incorrect",
        tolerance=0.001,
    )

    assert_true(
        result["basis"] < 0,
        "Expected negative forward basis",
    )

    print("Spot    :", spot)
    print("Forward :", result["implied_forward"])
    print("Basis   :", result["basis"])
    print("✅ PASS — Reasonable negative basis handled correctly")


# ============================================================
# TEST 3 — MULTI-STRIKE AGGREGATION
# ============================================================

def test_multi_strike_aggregation():
    engine = IndexOptionForwardEngine()

    t = 7 / 365
    expected_forward = 24425.0

    contracts = make_chain(
        strikes=[
            24250,
            24300,
            24350,
            24400,
            24450,
            24500,
            24550,
        ],
        forward=expected_forward,
        time_to_expiry=t,
    )

    result = engine.estimate_forward(
        contracts=contracts,
        spot_price=24400,
        time_to_expiry=t,
    )

    assert_true(
        result["forward_allowed"],
        "Valid multi-strike chain should produce forward",
    )

    assert_close(
        result["implied_forward"],
        expected_forward,
        "Median forward aggregation incorrect",
        tolerance=0.001,
    )

    assert_equal(
        result["valid_pair_count"],
        7,
        "All seven valid pairs should be accepted",
    )

    print("Valid Pairs :", result["valid_pair_count"])
    print("Forward     :", result["implied_forward"])
    print("✅ PASS — Multiple strikes aggregate correctly")


# ============================================================
# TEST 4 — REFERENCE STRIKE
# ============================================================

def test_reference_strike():
    engine = IndexOptionForwardEngine()

    t = 5 / 365

    contracts = make_chain(
        strikes=[
            24200,
            24250,
            24300,
            24350,
            24400,
            24450,
        ],
        forward=24385,
        time_to_expiry=t,
    )

    pairs = engine.build_strike_pairs(
        contracts
    )

    reference = engine.find_reference_strike(
        pairs=pairs,
        spot_price=24383.60,
    )

    assert_equal(
        reference,
        24400.0,
        "Nearest complete strike should be reference strike",
    )

    print("Spot             :", 24383.60)
    print("Reference Strike :", reference)
    print("✅ PASS — Reference strike selected nearest spot")


# ============================================================
# TEST 5 — MIDPOINT PREFERRED
# ============================================================

def test_midpoint_preferred():
    engine = IndexOptionForwardEngine()

    contract = make_contract(
        strike=24400,
        option_type="CE",
        price=500,
        bid=99,
        ask=101,
    )

    result = engine.choose_market_price(
        contract
    )

    assert_true(
        result["valid"],
        "Valid two-sided quote should be usable",
    )

    assert_close(
        result["price"],
        100,
        "Midpoint calculation incorrect",
    )

    assert_equal(
        result["source"],
        "MIDPOINT",
        "Midpoint must be preferred over LTP",
    )

    print("LTP    :", 500)
    print("Price  :", result["price"])
    print("Source :", result["source"])
    print("✅ PASS — Midpoint preferred over LTP")


# ============================================================
# TEST 6 — LTP FALLBACK
# ============================================================

def test_ltp_fallback():
    engine = IndexOptionForwardEngine()

    contract = make_contract(
        strike=24400,
        option_type="CE",
        price=125,
        bid=None,
        ask=None,
    )

    # make_contract generates defaults when None is supplied,
    # so explicitly remove the two-sided market.
    contract["bid"] = None
    contract["ask"] = None

    result = engine.choose_market_price(
        contract
    )

    assert_true(
        result["valid"],
        "Positive LTP should be usable as fallback",
    )

    assert_close(
        result["price"],
        125,
        "LTP fallback price incorrect",
    )

    assert_equal(
        result["source"],
        "LTP",
        "Expected LTP fallback",
    )

    print("Price  :", result["price"])
    print("Source :", result["source"])
    print("✅ PASS — LTP fallback works")


# ============================================================
# TEST 7 — WIDE SPREAD REJECTED
# ============================================================

def test_wide_spread_rejected():
    engine = IndexOptionForwardEngine(
        max_spread_pct=10.0
    )

    contract = make_contract(
        strike=24400,
        option_type="CE",
        price=100,
        bid=50,
        ask=150,
    )

    result = engine.choose_market_price(
        contract
    )

    assert_false(
        result["valid"],
        "Excessively wide spread must be rejected",
    )

    assert_equal(
        result["reason"],
        "SPREAD_TOO_WIDE",
        "Wrong wide-spread rejection reason",
    )

    print("Spread % :", result["spread_pct"])
    print("Reason   :", result["reason"])
    print("✅ PASS — Poor two-sided market rejected")


# ============================================================
# TEST 8 — NO VALID MARKET PRICE
# ============================================================

def test_no_market_price():
    engine = IndexOptionForwardEngine()

    contract = make_contract(
        strike=24400,
        option_type="CE",
        price=100,
    )

    contract["bid"] = None
    contract["ask"] = None
    contract["ltp"] = None

    result = engine.choose_market_price(
        contract
    )

    assert_false(
        result["valid"],
        "Contract without market price must reject",
    )

    assert_equal(
        result["reason"],
        "NO_VALID_MARKET_PRICE",
        "Wrong missing-price reason",
    )

    print("Reason :", result["reason"])
    print("✅ PASS — Missing market price rejected")


# ============================================================
# TEST 9 — MISSING OPTION LEG
# ============================================================

def test_missing_leg():
    engine = IndexOptionForwardEngine(
        min_valid_pairs=2
    )

    t = 5 / 365

    contracts = make_pair(
        strike=24400,
        forward=24390,
        time_to_expiry=t,
    )

    # Remove PE.
    contracts = [
        item
        for item in contracts
        if item["option_type"] == "CE"
    ]

    result = engine.estimate_forward(
        contracts=contracts,
        spot_price=24383.60,
        time_to_expiry=t,
    )

    assert_false(
        result["forward_allowed"],
        "Unmatched CE cannot independently create forward",
    )

    assert_equal(
        result["reason"],
        "NO_COMPLETE_CE_PE_PAIRS",
        "Wrong missing-pair block reason",
    )

    print("Permission :", result["forward_permission"])
    print("Reason     :", result["reason"])
    print("✅ PASS — Missing CE/PE pair cannot create forward")


# ============================================================
# TEST 10 — INSUFFICIENT VALID PAIRS
# ============================================================

def test_insufficient_pairs():
    engine = IndexOptionForwardEngine(
        min_valid_pairs=2
    )

    t = 5 / 365

    contracts = make_pair(
        strike=24400,
        forward=24390,
        time_to_expiry=t,
    )

    result = engine.estimate_forward(
        contracts=contracts,
        spot_price=24383.60,
        time_to_expiry=t,
    )

    assert_false(
        result["forward_allowed"],
        "One pair must not satisfy two-pair requirement",
    )

    assert_equal(
        result["reason"],
        "INSUFFICIENT_VALID_FORWARD_PAIRS",
        "Wrong insufficient-pair reason",
    )

    assert_equal(
        result["valid_pair_count"],
        1,
        "Exactly one pair should be valid",
    )

    print("Valid Pairs :", result["valid_pair_count"])
    print("Reason      :", result["reason"])
    print("✅ PASS — Minimum valid-pair requirement enforced")


# ============================================================
# TEST 11 — DUPLICATE LEG PROTECTION
# ============================================================

def test_duplicate_leg():
    engine = IndexOptionForwardEngine(
        min_valid_pairs=2
    )

    t = 5 / 365

    contracts = make_chain(
        strikes=[
            24350,
            24400,
            24450,
        ],
        forward=24390,
        time_to_expiry=t,
    )

    duplicate = deepcopy(
        contracts[0]
    )

    duplicate["tradingsymbol"] = (
        "DUPLICATE_CE"
    )

    contracts.append(
        duplicate
    )

    result = engine.estimate_forward(
        contracts=contracts,
        spot_price=24383.60,
        time_to_expiry=t,
    )

    duplicate_rejections = [
        item
        for item in result["rejections"]
        if item.get("reason")
        == "DUPLICATE_OPTION_LEG"
    ]

    assert_true(
        len(duplicate_rejections) >= 1,
        "Duplicate option leg must be explicitly rejected",
    )

    print("Duplicate Rejections :", len(duplicate_rejections))
    print("Valid Pairs          :", result["valid_pair_count"])
    print("✅ PASS — Duplicate option leg detected safely")


# ============================================================
# TEST 12 — INVALID SPOT
# ============================================================

def test_invalid_spot():
    engine = IndexOptionForwardEngine()

    result = engine.estimate_forward(
        contracts=[],
        spot_price=0,
        time_to_expiry=5 / 365,
    )

    assert_false(
        result["forward_allowed"],
        "Zero spot must block forward estimation",
    )

    assert_equal(
        result["reason"],
        "INVALID_FORWARD_INPUT",
        "Wrong invalid-input reason",
    )

    assert_true(
        "INVALID_SPOT_PRICE"
        in result["validation_errors"],
        "Invalid spot validation error missing",
    )

    print("Errors :", result["validation_errors"])
    print("✅ PASS — Invalid spot rejected")


# ============================================================
# TEST 13 — INVALID TIME
# ============================================================

def test_invalid_time():
    engine = IndexOptionForwardEngine()

    result = engine.estimate_forward(
        contracts=[],
        spot_price=24383.60,
        time_to_expiry=0,
    )

    assert_false(
        result["forward_allowed"],
        "Zero time must block forward estimation",
    )

    assert_true(
        "INVALID_TIME_TO_EXPIRY"
        in result["validation_errors"],
        "Invalid time validation error missing",
    )

    print("Errors :", result["validation_errors"])
    print("✅ PASS — Invalid time-to-expiry rejected")


# ============================================================
# TEST 14 — FORWARD DEVIATION PROTECTION
# ============================================================

def test_forward_deviation_protection():
    engine = IndexOptionForwardEngine(
        max_forward_deviation_pct=1.0,
        min_valid_pairs=2,
    )

    t = 5 / 365

    contracts = make_chain(
        strikes=[
            24350,
            24400,
            24450,
        ],
        forward=26000,
        time_to_expiry=t,
    )

    result = engine.estimate_forward(
        contracts=contracts,
        spot_price=24383.60,
        time_to_expiry=t,
    )

    assert_false(
        result["forward_allowed"],
        "Extreme forward must not be accepted",
    )

    assert_equal(
        result["reason"],
        "INSUFFICIENT_VALID_FORWARD_PAIRS",
        "Extreme estimates should leave insufficient valid pairs",
    )

    assert_equal(
        result["valid_pair_count"],
        0,
        "Extreme forward estimates should all reject",
    )

    print("Valid Pairs    :", result["valid_pair_count"])
    print("Rejected Pairs :", result["rejected_pair_count"])
    print("✅ PASS — Extreme forward deviation rejected")


# ============================================================
# TEST 15 — OUTLIER RESISTANCE
# ============================================================

def test_outlier_resistance():
    engine = IndexOptionForwardEngine(
        max_forward_deviation_pct=5.0,
        preferred_strikes_each_side=3,
        min_valid_pairs=2,
    )

    t = 5 / 365
    normal_forward = 24400.0

    contracts = make_chain(
        strikes=[
            24250,
            24300,
            24350,
            24400,
            24450,
            24500,
            24550,
        ],
        forward=normal_forward,
        time_to_expiry=t,
    )

    # Add a deliberately distorted pair farther from ATM.
    contracts.extend(
        make_pair(
            strike=24600,
            forward=25000,
            time_to_expiry=t,
        )
    )

    result = engine.estimate_forward(
        contracts=contracts,
        spot_price=24400,
        time_to_expiry=t,
    )

    assert_true(
        result["forward_allowed"],
        "One outlier must not destroy valid forward estimation",
    )

    assert_close(
        result["implied_forward"],
        normal_forward,
        "Robust aggregation failed to resist outlier",
        tolerance=0.01,
    )

    print("Expected Forward :", normal_forward)
    print("Final Forward    :", result["implied_forward"])
    print("Aggregation Pairs:", result["aggregation_pair_count"])
    print("✅ PASS — Forward aggregation resists isolated outlier")


# ============================================================
# TEST 16 — HIGH QUALITY CLASSIFICATION
# ============================================================

def test_high_quality():
    engine = IndexOptionForwardEngine()

    t = 5 / 365

    contracts = make_chain(
        strikes=[
            24250,
            24300,
            24350,
            24400,
            24450,
            24500,
            24550,
        ],
        forward=24400,
        time_to_expiry=t,
    )

    result = engine.estimate_forward(
        contracts=contracts,
        spot_price=24390,
        time_to_expiry=t,
    )

    assert_equal(
        result["quality"],
        "HIGH",
        "Tight multi-pair estimate should be HIGH quality",
    )

    print("Quality           :", result["quality"])
    print("Forward Range %   :", result["forward_range_pct"])
    print("Aggregation Pairs :", result["aggregation_pair_count"])
    print("✅ PASS — Strong forward agreement classified HIGH")


# ============================================================
# TEST 17 — LOW QUALITY CLASSIFICATION
# ============================================================

def test_low_quality():
    engine = IndexOptionForwardEngine()

    quality = engine._assess_quality(
        valid_pair_count=2,
        forward_range_pct=1.0,
        basis_pct=2.5,
    )

    assert_equal(
        quality,
        "LOW",
        "Weak forward estimate should classify LOW",
    )

    print("Quality :", quality)
    print("✅ PASS — Weak forward agreement classified LOW")


# ============================================================
# TEST 18 — INPUT IMMUTABILITY
# ============================================================

def test_input_immutability():
    engine = IndexOptionForwardEngine()

    t = 5 / 365

    contracts = make_chain(
        strikes=[
            24350,
            24400,
            24450,
        ],
        forward=24390,
        time_to_expiry=t,
    )

    original = deepcopy(
        contracts
    )

    result = engine.estimate_forward(
        contracts=contracts,
        spot_price=24383.60,
        time_to_expiry=t,
    )

    assert_true(
        result["forward_allowed"],
        "Valid chain should produce forward",
    )

    assert_equal(
        contracts,
        original,
        "Forward engine must not mutate market contracts",
    )

    print("Input Mutated : NO")
    print("✅ PASS — Input contracts remain immutable")


# ============================================================
# TEST 19 — DETERMINISTIC RESULT
# ============================================================

def test_deterministic_result():
    engine = IndexOptionForwardEngine()

    t = 5 / 365

    contracts = make_chain(
        strikes=[
            24250,
            24300,
            24350,
            24400,
            24450,
            24500,
            24550,
        ],
        forward=24410,
        time_to_expiry=t,
    )

    reversed_contracts = list(
        reversed(
            contracts
        )
    )

    first = engine.estimate_forward(
        contracts=contracts,
        spot_price=24400,
        time_to_expiry=t,
    )

    second = engine.estimate_forward(
        contracts=reversed_contracts,
        spot_price=24400,
        time_to_expiry=t,
    )

    assert_close(
        first["implied_forward"],
        second["implied_forward"],
        "Input ordering changed implied forward",
        tolerance=0.000001,
    )

    first_strikes = [
        item["strike"]
        for item in first["estimates"]
    ]

    second_strikes = [
        item["strike"]
        for item in second["estimates"]
    ]

    assert_equal(
        first_strikes,
        second_strikes,
        "Estimate ordering must remain deterministic",
    )

    print("Forward 1 :", first["implied_forward"])
    print("Forward 2 :", second["implied_forward"])
    print("✅ PASS — Forward calculation is deterministic")


# ============================================================
# TEST 20 — BASIS MATHEMATICS
# ============================================================

def test_basis_math():
    engine = IndexOptionForwardEngine()

    t = 5 / 365
    spot = 24383.60
    forward = 24403.60

    contracts = make_chain(
        strikes=[
            24350,
            24400,
            24450,
        ],
        forward=forward,
        time_to_expiry=t,
    )

    result = engine.estimate_forward(
        contracts=contracts,
        spot_price=spot,
        time_to_expiry=t,
    )

    expected_basis = 20.0

    expected_basis_pct = (
        expected_basis
        / spot
        * 100
    )

    assert_close(
        result["basis"],
        expected_basis,
        "Forward basis calculation incorrect",
        tolerance=0.001,
    )

    assert_close(
        result["basis_pct"],
        expected_basis_pct,
        "Forward basis percentage incorrect",
        tolerance=0.0001,
    )

    print("Basis   :", result["basis"])
    print("Basis % :", result["basis_pct"])
    print("✅ PASS — Forward basis mathematics correct")


# ============================================================
# TEST 21 — ANALYTICAL AUTHORITY ONLY
# ============================================================

def test_zero_authority():
    engine = IndexOptionForwardEngine()

    t = 5 / 365

    contracts = make_chain(
        strikes=[
            24350,
            24400,
            24450,
        ],
        forward=24390,
        time_to_expiry=t,
    )

    result = engine.estimate_forward(
        contracts=contracts,
        spot_price=24383.60,
        time_to_expiry=t,
    )

    assert_true(
        result["forward_allowed"],
        "Valid analytical result expected",
    )

    assert_false(
        result["contract_selection_allowed"],
        "Forward engine cannot select contracts",
    )

    assert_false(
        result["trade_decision_allowed"],
        "Forward engine cannot make trade decisions",
    )

    assert_false(
        result["risk_allocation_allowed"],
        "Forward engine cannot allocate risk",
    )

    assert_false(
        result["position_sizing_allowed"],
        "Forward engine cannot size positions",
    )

    assert_false(
        result["broker_order_allowed"],
        "Forward engine cannot authorize broker orders",
    )

    forbidden_fields = [
        "selected_contract",
        "selected_strike",
        "approved_risk",
        "authorized_risk",
        "final_lots",
        "final_quantity",
        "order_id",
        "transaction_type",
    ]

    for field in forbidden_fields:
        if field in result:
            raise AssertionError(
                "Forward engine illegally contains authority: "
                f"{field}"
            )

    print("Forward Analytics   : YES")
    print("Contract Selection  : NO")
    print("Trade Decision      : NO")
    print("Risk Allocation     : NO")
    print("Position Sizing     : NO")
    print("Broker Execution    : NO")
    print()
    print("✅ PASS — Forward engine remains analytical only")


# ============================================================
# TEST 22 — BLOCKED RESULT ZERO AUTHORITY
# ============================================================

def test_blocked_zero_authority():
    engine = IndexOptionForwardEngine()

    result = engine.estimate_forward(
        contracts=[],
        spot_price=0,
        time_to_expiry=0,
    )

    assert_equal(
        result["forward_permission"],
        "BLOCK",
        "Invalid forward request must block",
    )

    assert_false(
        result["forward_allowed"],
        "Blocked forward result cannot be allowed",
    )

    assert_false(
        result["contract_selection_allowed"],
        "Blocked result cannot select contracts",
    )

    assert_false(
        result["trade_decision_allowed"],
        "Blocked result cannot create trade decision",
    )

    assert_false(
        result["risk_allocation_allowed"],
        "Blocked result cannot allocate risk",
    )

    assert_false(
        result["position_sizing_allowed"],
        "Blocked result cannot size positions",
    )

    assert_false(
        result["broker_order_allowed"],
        "Blocked result cannot authorize orders",
    )

    print("Permission           :", result["forward_permission"])
    print("Forward Allowed      :", result["forward_allowed"])
    print("Broker Order Allowed :", result["broker_order_allowed"])
    print()
    print(
        "✅ PASS — Blocked forward result has zero downstream authority"
    )


# ============================================================
# TEST 23 — REALISTIC NIFTY-LIKE PARITY
# ============================================================

def test_realistic_nifty_like_parity():
    """
    Uses prices similar in shape to the live chain we observed.

    This does not assert what the real market forward must be.
    It verifies the parity formula behaves correctly.
    """

    engine = IndexOptionForwardEngine()

    spot = 24383.60
    strike = 24400.0
    t = 4 / 365

    ce_midpoint = (
        67.80 + 68.20
    ) / 2

    pe_midpoint = (
        98.95 + 99.50
    ) / 2

    forward = engine.estimate_forward_at_strike(
        strike=strike,
        call_price=ce_midpoint,
        put_price=pe_midpoint,
        time_to_expiry=t,
    )

    expected = (
        strike
        + math.exp(
            0.06 * t
        )
        * (
            ce_midpoint
            - pe_midpoint
        )
    )

    assert_close(
        forward,
        expected,
        "NIFTY-like parity calculation incorrect",
        tolerance=0.000001,
    )

    assert_true(
        forward < spot,
        "Given observed CE/PE relationship should imply forward below spot",
    )

    print("Spot            :", spot)
    print("Strike          :", strike)
    print("CE Midpoint     :", ce_midpoint)
    print("PE Midpoint     :", pe_midpoint)
    print("Implied Forward :", forward)
    print("Basis           :", forward - spot)
    print()
    print("✅ PASS — Live-like CE/PE relationship handled correctly")


# ============================================================
# TEST 24 — EMPTY CONTRACT COLLECTION
# ============================================================

def test_empty_contracts():
    engine = IndexOptionForwardEngine()

    result = engine.estimate_forward(
        contracts=[],
        spot_price=24383.60,
        time_to_expiry=5 / 365,
    )

    assert_false(
        result["forward_allowed"],
        "Empty contract collection must not produce forward",
    )

    assert_equal(
        result["reason"],
        "NO_COMPLETE_CE_PE_PAIRS",
        "Wrong empty-chain reason",
    )

    print("Reason :", result["reason"])
    print("✅ PASS — Empty option chain safely blocked")


# ============================================================
# TEST 25 — INVALID SINGLE STRIKE INPUT
# ============================================================

def test_invalid_single_strike_input():
    engine = IndexOptionForwardEngine()

    forward = engine.estimate_forward_at_strike(
        strike=0,
        call_price=100,
        put_price=100,
        time_to_expiry=5 / 365,
    )

    assert_equal(
        forward,
        None,
        "Invalid strike must not produce forward",
    )

    print("Forward :", forward)
    print("✅ PASS — Invalid parity input rejected")


# ============================================================
# TEST RUNNER
# ============================================================

def run_all_tests():
    global PASSED
    global TOTAL

    tests = [
        (
            "TEST 1 — SINGLE STRIKE FORWARD RECOVERY",
            test_single_strike_forward_recovery,
        ),
        (
            "TEST 2 — NEGATIVE BASIS",
            test_negative_basis,
        ),
        (
            "TEST 3 — MULTI-STRIKE AGGREGATION",
            test_multi_strike_aggregation,
        ),
        (
            "TEST 4 — REFERENCE STRIKE",
            test_reference_strike,
        ),
        (
            "TEST 5 — MIDPOINT PREFERRED",
            test_midpoint_preferred,
        ),
        (
            "TEST 6 — LTP FALLBACK",
            test_ltp_fallback,
        ),
        (
            "TEST 7 — WIDE SPREAD REJECTED",
            test_wide_spread_rejected,
        ),
        (
            "TEST 8 — NO VALID MARKET PRICE",
            test_no_market_price,
        ),
        (
            "TEST 9 — MISSING OPTION LEG",
            test_missing_leg,
        ),
        (
            "TEST 10 — INSUFFICIENT VALID PAIRS",
            test_insufficient_pairs,
        ),
        (
            "TEST 11 — DUPLICATE LEG PROTECTION",
            test_duplicate_leg,
        ),
        (
            "TEST 12 — INVALID SPOT",
            test_invalid_spot,
        ),
        (
            "TEST 13 — INVALID TIME",
            test_invalid_time,
        ),
        (
            "TEST 14 — FORWARD DEVIATION PROTECTION",
            test_forward_deviation_protection,
        ),
        (
            "TEST 15 — OUTLIER RESISTANCE",
            test_outlier_resistance,
        ),
        (
            "TEST 16 — HIGH QUALITY CLASSIFICATION",
            test_high_quality,
        ),
        (
            "TEST 17 — LOW QUALITY CLASSIFICATION",
            test_low_quality,
        ),
        (
            "TEST 18 — INPUT IMMUTABILITY",
            test_input_immutability,
        ),
        (
            "TEST 19 — DETERMINISTIC RESULT",
            test_deterministic_result,
        ),
        (
            "TEST 20 — BASIS MATHEMATICS",
            test_basis_math,
        ),
        (
            "TEST 21 — ANALYTICAL AUTHORITY ONLY",
            test_zero_authority,
        ),
        (
            "TEST 22 — BLOCKED ZERO AUTHORITY",
            test_blocked_zero_authority,
        ),
        (
            "TEST 23 — REALISTIC NIFTY-LIKE PARITY",
            test_realistic_nifty_like_parity,
        ),
        (
            "TEST 24 — EMPTY CONTRACT COLLECTION",
            test_empty_contracts,
        ),
        (
            "TEST 25 — INVALID SINGLE STRIKE INPUT",
            test_invalid_single_strike_input,
        ),
    ]

    TOTAL = len(
        tests
    )

    heading(
        "THETA AI TRADER — INDEX OPTION FORWARD ENGINE TEST SUITE"
    )

    print()
    print("Live Zerodha Calls : NONE")
    print("Real Orders        : NONE")
    print("Broker Execution   : DISABLED")
    print("Forward Method     : CE/PE PUT-CALL PARITY")
    print("Aggregation        : ROBUST MEDIAN")

    for title, test in tests:

        heading(
            title
        )

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
                "❌ INDEX OPTION FORWARD ENGINE TESTS FAILED "
                f"({PASSED}/{TOTAL} passed)"
            )

            line()

            raise

    print()
    line()

    print(
        "✅ ALL INDEX OPTION FORWARD ENGINE TESTS PASSED "
        f"({PASSED}/{TOTAL})"
    )

    print(
        "🔒 FORWARD ENGINE IS ANALYTICAL ONLY"
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