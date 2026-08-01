# ============================================================
# THETA AI TRADER
# OPTION MARKET INTELLIGENCE ENGINE — OFFLINE TEST SUITE
# ============================================================

from copy import deepcopy

from option_market_intelligence_engine import (
    OptionMarketIntelligenceEngine,
)


# ============================================================
# TEST HELPERS
# ============================================================

TOTAL_TESTS = 0
PASSED_TESTS = 0


def heading(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def pass_test(message):
    global PASSED_TESTS
    PASSED_TESTS += 1
    print(f"✅ PASS — {message}")


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}"
        )


def assert_close(actual, expected, tolerance=1e-9, message="Values differ"):
    if actual is None:
        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : None"
        )

    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}"
        )


def run_case(number, title, function):
    global TOTAL_TESTS

    TOTAL_TESTS += 1

    print("\n" + "-" * 78)
    print(f"TEST {number} — {title}")
    print("-" * 78)

    function()


# ============================================================
# SYNTHETIC FORWARD-MODE OPTION CHAIN
# ============================================================

def make_contract(
    strike,
    option_type,
    price,
    oi,
    iv,
    delta,
    gamma,
    theta,
    vega,
    implied_forward=24368.70,
):
    option_type = option_type.upper()

    return {
        "tradingsymbol": f"TEST{int(strike)}{option_type}",
        "expiry": "2026-08-04",
        "strike": float(strike),
        "option_type": option_type,

        "ltp": float(price),
        "bid": max(float(price) - 0.10, 0.05),
        "ask": float(price) + 0.10,
        "oi": float(oi),

        "iv_decimal": float(iv) / 100.0,
        "iv": float(iv),

        "delta": float(delta),
        "gamma": float(gamma),
        "theta": float(theta),
        "vega": float(vega),

        "greeks_market_price": float(price),
        "greeks_price_source": "MIDPOINT",

        "time_to_expiry_years": 0.01,
        "risk_free_rate": 0.065,

        "pricing_mode": "FORWARD",
        "implied_forward": float(implied_forward),

        "delta_basis": "FORWARD",
        "gamma_basis": "FORWARD",

        # Deliberately false upstream authority.
        "broker_order_allowed": False,
    }


def build_chain():
    """
    Seven complete CE/PE pairs.

    Forward = 24368.70
    Nearest strike = 24350
    Strike step = 50
    """

    rows = [
        # strike, CE price, PE price, CE OI, PE OI,
        # CE IV, PE IV, CE delta, PE delta,
        # gamma, CE theta, PE theta, CE vega, PE vega

        (
            24200,
            190.0, 22.0,
            3000, 9000,
            10.2, 10.8,
            0.80, -0.20,
            0.0011,
            -8.0, -8.2,
            6.0, 6.1,
        ),

        (
            24250,
            155.0, 35.0,
            4000, 11000,
            9.6, 10.1,
            0.71, -0.29,
            0.0014,
            -9.0, -9.1,
            7.2, 7.3,
        ),

        (
            24300,
            120.0, 50.0,
            5000, 15000,
            9.0, 9.4,
            0.62, -0.38,
            0.0017,
            -10.0, -10.1,
            8.3, 8.4,
        ),

        (
            24350,
            90.0, 72.0,
            7000, 13000,
            8.7, 8.9,
            0.53, -0.47,
            0.0020,
            -11.0, -11.1,
            9.5, 9.6,
        ),

        (
            24400,
            65.0, 100.0,
            18000, 8000,
            8.6, 8.7,
            0.44, -0.56,
            0.0019,
            -10.8, -10.9,
            9.3, 9.4,
        ),

        (
            24450,
            45.0, 130.0,
            14000, 5000,
            8.8, 8.6,
            0.34, -0.66,
            0.0016,
            -9.5, -9.4,
            8.4, 8.3,
        ),

        (
            24500,
            30.0, 165.0,
            10000, 3000,
            9.5, 8.9,
            0.25, -0.75,
            0.0012,
            -8.0, -7.9,
            7.0, 6.9,
        ),
    ]

    contracts = []

    for row in rows:
        (
            strike,
            ce_price,
            pe_price,
            ce_oi,
            pe_oi,
            ce_iv,
            pe_iv,
            ce_delta,
            pe_delta,
            gamma,
            ce_theta,
            pe_theta,
            ce_vega,
            pe_vega,
        ) = row

        contracts.append(
            make_contract(
                strike=strike,
                option_type="CE",
                price=ce_price,
                oi=ce_oi,
                iv=ce_iv,
                delta=ce_delta,
                gamma=gamma,
                theta=ce_theta,
                vega=ce_vega,
            )
        )

        contracts.append(
            make_contract(
                strike=strike,
                option_type="PE",
                price=pe_price,
                oi=pe_oi,
                iv=pe_iv,
                delta=pe_delta,
                gamma=gamma,
                theta=pe_theta,
                vega=pe_vega,
            )
        )

    return contracts


def build_greeks_result(contracts=None):
    if contracts is None:
        contracts = build_chain()

    return {
        "greeks_permission": "ALLOW",
        "greeks_allowed": True,
        "reason": "GREEKS_CALCULATED",
        "contracts": contracts,
        "pricing_mode": "FORWARD",
        "implied_forward": 24368.70,
        "broker_order_allowed": False,
    }


def assert_no_authority(result):
    authority_fields = [
        "contract_selection_allowed",
        "trade_decision_allowed",
        "strategy_selection_allowed",
        "risk_allocation_allowed",
        "position_sizing_allowed",
        "broker_order_allowed",
    ]

    for field in authority_fields:
        assert_equal(
            result.get(field),
            False,
            f"{field} must always remain False",
        )


# ============================================================
# TESTS
# ============================================================

def test_1():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        contracts=build_chain(),
        spot_price=24383.60,
        implied_forward=24368.70,
    )

    assert_equal(
        result["intelligence_permission"],
        "ALLOW",
        "Valid chain should be allowed",
    )

    assert_equal(
        result["intelligence_allowed"],
        True,
        "Intelligence should be allowed",
    )

    assert_equal(
        result["valid_contract_count"],
        14,
        "All synthetic contracts should be valid",
    )

    assert_equal(
        result["complete_pair_count"],
        7,
        "Seven complete CE/PE pairs expected",
    )

    assert_no_authority(result)

    pass_test(
        "Valid enriched FORWARD chain accepted"
    )


def test_2():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        contracts=build_chain(),
        spot_price=24383.60,
        implied_forward=24368.70,
    )

    analytics = result["analytics"]

    assert_close(
        analytics["reference_price"],
        24368.70,
        message="Forward should be analytical reference",
    )

    assert_equal(
        analytics["reference_source"],
        "IMPLIED_FORWARD",
        "Reference source should be implied forward",
    )

    assert_close(
        analytics["atm_strike"],
        24350.0,
        message="ATM must be nearest strike to forward",
    )

    assert_close(
        analytics["strike_step"],
        50.0,
        message="Strike step should be detected correctly",
    )

    pass_test(
        "FORWARD reference, ATM and strike step detected"
    )


def test_3():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        build_chain(),
        spot_price=24383.60,
        implied_forward=24368.70,
    )

    straddle = (
        result["analytics"]["atm_straddle"]
    )

    assert_equal(
        straddle["available"],
        True,
        "ATM straddle should be available",
    )

    assert_close(
        straddle["ce_price"],
        90.0,
        message="ATM CE price incorrect",
    )

    assert_close(
        straddle["pe_price"],
        72.0,
        message="ATM PE price incorrect",
    )

    assert_close(
        straddle["straddle_price"],
        162.0,
        message="ATM straddle should equal CE + PE",
    )

    pass_test(
        "ATM straddle calculated correctly"
    )


def test_4():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        build_chain(),
        spot_price=24383.60,
        implied_forward=24368.70,
    )

    straddle = (
        result["analytics"]["atm_straddle"]
    )

    assert_close(
        straddle["expected_move_points"],
        162.0,
        message="Expected move proxy incorrect",
    )

    expected_percent = (
        162.0 / 24368.70 * 100.0
    )

    assert_close(
        straddle["expected_move_percent"],
        expected_percent,
        tolerance=1e-9,
        message="Expected move percentage incorrect",
    )

    assert_close(
        straddle["lower_implied_range"],
        24368.70 - 162.0,
        message="Lower implied range incorrect",
    )

    assert_close(
        straddle["upper_implied_range"],
        24368.70 + 162.0,
        message="Upper implied range incorrect",
    )

    assert_equal(
        straddle["expected_move_method"],
        "ATM_STRADDLE_PREMIUM_PROXY",
        "Expected move must remain explicitly labelled proxy",
    )

    pass_test(
        "Expected-move proxy and implied range calculated"
    )


def test_5():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        build_chain(),
        implied_forward=24368.70,
    )

    oi = result["analytics"]["oi"]

    expected_ce = (
        3000 + 4000 + 5000 + 7000
        + 18000 + 14000 + 10000
    )

    expected_pe = (
        9000 + 11000 + 15000 + 13000
        + 8000 + 5000 + 3000
    )

    assert_close(
        oi["total_ce_oi"],
        expected_ce,
        message="Total CE OI incorrect",
    )

    assert_close(
        oi["total_pe_oi"],
        expected_pe,
        message="Total PE OI incorrect",
    )

    assert_close(
        oi["oi_pcr"],
        expected_pe / expected_ce,
        message="OI PCR incorrect",
    )

    pass_test(
        "Total CE/PE OI and PCR calculated correctly"
    )


def test_6():
    engine = OptionMarketIntelligenceEngine(
        wall_count=3
    )

    result = engine.analyze_option_chain(
        build_chain(),
        implied_forward=24368.70,
    )

    oi = result["analytics"]["oi"]

    assert_close(
        oi["highest_call_wall"]["strike"],
        24400.0,
        message="Highest CE OI wall incorrect",
    )

    assert_close(
        oi["highest_put_wall"]["strike"],
        24300.0,
        message="Highest PE OI wall incorrect",
    )

    assert_equal(
        len(oi["call_walls"]),
        3,
        "Three call walls expected",
    )

    assert_equal(
        len(oi["put_walls"]),
        3,
        "Three put walls expected",
    )

    pass_test(
        "Call and put OI walls ranked correctly"
    )


def test_7():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        build_chain(),
        implied_forward=24368.70,
    )

    structure = (
        result["analytics"][
            "market_structure"
        ]
    )

    assert_close(
        structure[
            "oi_resistance_candidate"
        ],
        24400.0,
        message="OI resistance candidate incorrect",
    )

    assert_close(
        structure[
            "oi_support_candidate"
        ],
        24300.0,
        message="OI support candidate incorrect",
    )

    assert_equal(
        structure["is_trade_signal"],
        False,
        "OI structure must never become a trade signal",
    )

    pass_test(
        "OI market structure remains analytical only"
    )


def test_8():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        build_chain(),
        implied_forward=24368.70,
    )

    iv = result["analytics"]["iv"]

    assert_close(
        iv["atm_ce_iv"],
        8.7,
        message="ATM CE IV incorrect",
    )

    assert_close(
        iv["atm_pe_iv"],
        8.9,
        message="ATM PE IV incorrect",
    )

    assert_close(
        iv["atm_average_iv"],
        8.8,
        message="ATM average IV incorrect",
    )

    assert_close(
        iv["atm_ce_minus_pe_iv"],
        -0.2,
        tolerance=1e-9,
        message="ATM CE-PE IV skew incorrect",
    )

    pass_test(
        "ATM IV and CE/PE IV difference calculated"
    )


def test_9():
    engine = OptionMarketIntelligenceEngine(
        wing_distance_steps=3
    )

    result = engine.analyze_option_chain(
        build_chain(),
        implied_forward=24368.70,
    )

    iv = result["analytics"]["iv"]

    assert_close(
        iv["lower_wing"]["strike"],
        24200.0,
        message="Lower wing strike incorrect",
    )

    assert_close(
        iv["upper_wing"]["strike"],
        24500.0,
        message="Upper wing strike incorrect",
    )

    assert_true(
        iv["lower_minus_upper_wing_iv"]
        is not None,
        "Wing skew should be available",
    )

    pass_test(
        "IV wing/smile structure calculated"
    )


def test_10():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        build_chain(),
        implied_forward=24368.70,
    )

    greeks = result["analytics"]["greeks"]

    highest = (
        greeks[
            "highest_combined_gamma_strike"
        ]
    )

    assert_close(
        highest["strike"],
        24350.0,
        message="Highest combined gamma strike incorrect",
    )

    assert_close(
        highest["combined_gamma"],
        0.004,
        tolerance=1e-12,
        message="Combined gamma incorrect",
    )

    pass_test(
        "Gamma concentration detected correctly"
    )


def test_11():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        build_chain(),
        implied_forward=24368.70,
    )

    greeks = result["analytics"]["greeks"]

    assert_true(
        greeks["total_absolute_theta"] > 0,
        "Total theta should be positive after absolute aggregation",
    )

    assert_true(
        greeks["total_vega"] > 0,
        "Total vega should be positive",
    )

    assert_close(
        greeks["total_vega"],
        (
            greeks["ce_vega"]
            + greeks["pe_vega"]
        ),
        message="CE + PE vega should equal total vega",
    )

    pass_test(
        "Theta and vega structure aggregated correctly"
    )


def test_12():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        build_chain(),
        implied_forward=24368.70,
    )

    analytics = result["analytics"]

    assert_equal(
        analytics["pricing_modes"],
        ["FORWARD"],
        "Pricing mode semantics should remain FORWARD",
    )

    assert_equal(
        analytics["delta_bases"],
        ["FORWARD"],
        "Delta basis should remain FORWARD",
    )

    assert_equal(
        analytics["gamma_bases"],
        ["FORWARD"],
        "Gamma basis should remain FORWARD",
    )

    pass_test(
        "Black-76 FORWARD Greek semantics preserved"
    )


def test_13():
    engine = OptionMarketIntelligenceEngine()

    chain = build_chain()
    original = deepcopy(chain)

    engine.analyze_option_chain(
        chain,
        implied_forward=24368.70,
    )

    assert_equal(
        chain,
        original,
        "Engine must not mutate upstream contracts",
    )

    pass_test(
        "Input option-chain data remains immutable"
    )


def test_14():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        None
    )

    assert_equal(
        result["intelligence_permission"],
        "BLOCK",
        "None input must block",
    )

    assert_equal(
        result["intelligence_allowed"],
        False,
        "None input cannot create intelligence",
    )

    assert_no_authority(result)

    pass_test(
        "None contract collection safely blocked"
    )


def test_15():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        []
    )

    assert_equal(
        result["intelligence_permission"],
        "BLOCK",
        "Empty chain must block",
    )

    assert_equal(
        result["reason"],
        "EMPTY_CONTRACT_COLLECTION",
        "Wrong empty-chain block reason",
    )

    assert_no_authority(result)

    pass_test(
        "Empty option chain safely blocked"
    )


def test_16():
    engine = OptionMarketIntelligenceEngine()

    malformed = [
        {
            "tradingsymbol": "BROKEN",
            "strike": 24350,
            "option_type": "CE",
        }
    ]

    result = engine.analyze_option_chain(
        malformed,
        implied_forward=24368.70,
    )

    assert_equal(
        result["intelligence_permission"],
        "BLOCK",
        "Malformed chain must block",
    )

    assert_equal(
        result["reason"],
        "NO_VALID_ENRICHED_CONTRACTS",
        "Malformed contracts should be rejected",
    )

    assert_no_authority(result)

    pass_test(
        "Malformed enriched contracts rejected"
    )


def test_17():
    engine = OptionMarketIntelligenceEngine(
        minimum_complete_pairs=3
    )

    chain = [
        make_contract(
            24350, "CE",
            90, 7000, 8.7,
            0.53, 0.002,
            -11, 9.5,
        ),
        make_contract(
            24350, "PE",
            72, 13000, 8.9,
            -0.47, 0.002,
            -11.1, 9.6,
        ),
    ]

    result = engine.analyze_option_chain(
        chain,
        implied_forward=24368.70,
    )

    assert_equal(
        result["intelligence_permission"],
        "BLOCK",
        "Insufficient pair count must block",
    )

    assert_equal(
        result["reason"],
        "INSUFFICIENT_COMPLETE_OPTION_PAIRS",
        "Wrong incomplete-chain block reason",
    )

    assert_no_authority(result)

    pass_test(
        "Insufficient CE/PE pair coverage blocked"
    )


def test_18():
    engine = OptionMarketIntelligenceEngine()

    chain = build_chain()

    # Remove every source of reference price.
    for contract in chain:
        contract["implied_forward"] = None

    result = engine.analyze_option_chain(
        chain,
        spot_price=None,
        implied_forward=None,
    )

    assert_equal(
        result["intelligence_permission"],
        "BLOCK",
        "Missing reference price must block",
    )

    assert_equal(
        result["reason"],
        "REFERENCE_PRICE_UNAVAILABLE",
        "Wrong reference-price block reason",
    )

    assert_no_authority(result)

    pass_test(
        "Missing spot/forward reference safely blocked"
    )


def test_19():
    engine = OptionMarketIntelligenceEngine()

    blocked_greeks = {
        "greeks_permission": "BLOCK",
        "greeks_allowed": False,
        "reason": "TEST_UPSTREAM_BLOCK",
        "contracts": build_chain(),

        # Malicious/invalid authority claim.
        "broker_order_allowed": True,
    }

    result = engine.analyze_greeks_result(
        blocked_greeks,
        spot_price=24383.60,
    )

    assert_equal(
        result["intelligence_permission"],
        "BLOCK",
        "Blocked Greeks must remain blocked",
    )

    assert_equal(
        result["reason"],
        "UPSTREAM_GREEKS_BLOCKED",
        "Wrong upstream-block reason",
    )

    assert_no_authority(result)

    pass_test(
        "Blocked Greeks result cannot become allowed intelligence"
    )


def test_20():
    engine = OptionMarketIntelligenceEngine()

    greeks_result = build_greeks_result()

    result = engine.analyze_greeks_result(
        greeks_result,
        spot_price=24383.60,
    )

    assert_equal(
        result["intelligence_permission"],
        "ALLOW",
        "Valid Greeks result should be consumable",
    )

    assert_equal(
        result["upstream_greeks_permission"],
        "ALLOW",
        "Upstream permission not preserved",
    )

    assert_equal(
        result["upstream_pricing_mode"],
        "FORWARD",
        "Upstream pricing mode not preserved",
    )

    assert_no_authority(result)

    pass_test(
        "Exact OptionGreeksEngine result contract supported"
    )


def test_21():
    engine = OptionMarketIntelligenceEngine()

    greeks_result = build_greeks_result()

    # Simulate malformed/malicious upstream authority.
    greeks_result["broker_order_allowed"] = True
    greeks_result["trade_decision_allowed"] = True
    greeks_result["risk_allocation_allowed"] = True

    result = engine.analyze_greeks_result(
        greeks_result,
        spot_price=24383.60,
    )

    assert_equal(
        result["intelligence_permission"],
        "ALLOW",
        "Analytics may still be calculated",
    )

    assert_no_authority(result)

    pass_test(
        "Upstream authority claims cannot leak into intelligence engine"
    )


def test_22():
    engine = OptionMarketIntelligenceEngine()

    chain = build_chain()

    # Corrupt one contract only.
    chain[0]["iv"] = None

    result = engine.analyze_option_chain(
        chain,
        implied_forward=24368.70,
    )

    assert_equal(
        result["intelligence_permission"],
        "ALLOW",
        "One rejected contract should not necessarily destroy chain",
    )

    assert_equal(
        result["rejected_contract_count"],
        1,
        "Exactly one contract should be rejected",
    )

    assert_true(
        "SOME_INPUT_CONTRACTS_REJECTED"
        in result["warnings"],
        "Partial rejection warning expected",
    )

    assert_no_authority(result)

    pass_test(
        "Partial malformed data degrades safely without authority escalation"
    )


def test_23():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        build_chain(),
        implied_forward=24368.70,
    )

    quality = result["data_quality"]

    assert_equal(
        quality["status"],
        "HIGH",
        "Complete clean synthetic chain should have HIGH data quality",
    )

    assert_close(
        quality["score"],
        100.0,
        message="Clean synthetic chain should score 100",
    )

    pass_test(
        "Data-quality diagnostics report clean chain correctly"
    )


def test_24():
    engine = OptionMarketIntelligenceEngine()

    chain = build_chain()

    # Force inconsistent pricing semantics.
    chain[0]["pricing_mode"] = "SPOT"

    result = engine.analyze_option_chain(
        chain,
        implied_forward=24368.70,
    )

    quality = result["data_quality"]

    assert_true(
        "MIXED_PRICING_MODES"
        in quality["warnings"],
        "Mixed pricing mode warning expected",
    )

    assert_true(
        quality["score"] < 100.0,
        "Mixed pricing semantics must reduce quality",
    )

    assert_no_authority(result)

    pass_test(
        "Mixed pricing semantics detected by quality controls"
    )


def test_25():
    engine = OptionMarketIntelligenceEngine()

    chain = build_chain()

    # Make all OI zero.
    for contract in chain:
        contract["oi"] = 0

    result = engine.analyze_option_chain(
        chain,
        implied_forward=24368.70,
    )

    oi = result["analytics"]["oi"]

    assert_equal(
        oi["oi_pcr"],
        None,
        "PCR must not divide by zero",
    )

    assert_close(
        oi["total_ce_oi"],
        0.0,
        message="CE OI should be zero",
    )

    assert_close(
        oi["total_pe_oi"],
        0.0,
        message="PE OI should be zero",
    )

    assert_no_authority(result)

    pass_test(
        "Zero-OI chain handled without division errors"
    )


def test_26():
    engine = OptionMarketIntelligenceEngine()

    chain = build_chain()

    # Duplicate a CE contract at 24400 with lower OI.
    duplicate = deepcopy(
        next(
            contract
            for contract in chain
            if (
                contract["strike"] == 24400
                and contract["option_type"] == "CE"
            )
        )
    )

    duplicate["tradingsymbol"] = "LOWER_OI_DUPLICATE"
    duplicate["oi"] = 1

    chain.append(duplicate)

    result = engine.analyze_option_chain(
        chain,
        implied_forward=24368.70,
    )

    assert_equal(
        result["intelligence_permission"],
        "ALLOW",
        "Duplicate contract should not crash analytics",
    )

    assert_close(
        result["analytics"]["oi"][
            "highest_call_wall"
        ]["strike"],
        24400.0,
        message="Correct high-OI wall should remain intact",
    )

    assert_no_authority(result)

    pass_test(
        "Duplicate strike/type input handled defensively"
    )


def test_27():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        build_chain(),
        implied_forward=24368.70,
    )

    analytics = result["analytics"]

    assert_equal(
        analytics["market_structure"][
            "is_trade_signal"
        ],
        False,
        "Market structure cannot create trade signal",
    )

    assert_true(
        "selected_contract"
        not in result,
        "Intelligence result must not select a contract",
    )

    assert_true(
        "quantity"
        not in result,
        "Intelligence result must not create quantity",
    )

    assert_true(
        "order"
        not in result,
        "Intelligence result must not create order",
    )

    assert_true(
        "transaction_type"
        not in result,
        "Intelligence result must not create BUY/SELL intent",
    )

    assert_no_authority(result)

    pass_test(
        "Intelligence output contains no trade/order intent"
    )


def test_28():
    engine = OptionMarketIntelligenceEngine()

    result = engine.analyze_option_chain(
        build_chain(),
        implied_forward=24368.70,
    )

    # Final safety invariant audit.
    assert_equal(
        result["intelligence_allowed"],
        True,
        "Analytics should be available",
    )

    assert_no_authority(result)

    assert_equal(
        result["analytics"]["market_structure"][
            "interpretation"
        ],
        "OI_CONCENTRATION_ONLY",
        "OI structure must retain analytical interpretation",
    )

    assert_equal(
        result["analytics"]["atm_straddle"][
            "expected_move_method"
        ],
        "ATM_STRADDLE_PREMIUM_PROXY",
        "Expected move must retain proxy label",
    )

    pass_test(
        "Allowed intelligence mathematically retains zero execution authority"
    )


# ============================================================
# RUNNER
# ============================================================

def run_tests():
    heading(
        "THETA AI TRADER — OPTION MARKET INTELLIGENCE ENGINE TESTS"
    )

    tests = [
        (
            1,
            "VALID FORWARD-MODE CHAIN",
            test_1,
        ),
        (
            2,
            "FORWARD REFERENCE / ATM / STRIKE STEP",
            test_2,
        ),
        (
            3,
            "ATM STRADDLE",
            test_3,
        ),
        (
            4,
            "EXPECTED MOVE PROXY",
            test_4,
        ),
        (
            5,
            "OI TOTALS + PCR",
            test_5,
        ),
        (
            6,
            "CALL / PUT OI WALLS",
            test_6,
        ),
        (
            7,
            "OI MARKET STRUCTURE",
            test_7,
        ),
        (
            8,
            "ATM IV STRUCTURE",
            test_8,
        ),
        (
            9,
            "IV WINGS / SMILE",
            test_9,
        ),
        (
            10,
            "GAMMA CONCENTRATION",
            test_10,
        ),
        (
            11,
            "THETA / VEGA STRUCTURE",
            test_11,
        ),
        (
            12,
            "FORWARD GREEKS SEMANTICS",
            test_12,
        ),
        (
            13,
            "INPUT IMMUTABILITY",
            test_13,
        ),
        (
            14,
            "NONE INPUT BLOCK",
            test_14,
        ),
        (
            15,
            "EMPTY INPUT BLOCK",
            test_15,
        ),
        (
            16,
            "MALFORMED CONTRACT BLOCK",
            test_16,
        ),
        (
            17,
            "INSUFFICIENT COMPLETE PAIRS",
            test_17,
        ),
        (
            18,
            "REFERENCE PRICE REQUIRED",
            test_18,
        ),
        (
            19,
            "BLOCKED UPSTREAM GREEKS",
            test_19,
        ),
        (
            20,
            "GREEKS RESULT INTEGRATION",
            test_20,
        ),
        (
            21,
            "UPSTREAM AUTHORITY ISOLATION",
            test_21,
        ),
        (
            22,
            "PARTIAL CONTRACT REJECTION",
            test_22,
        ),
        (
            23,
            "DATA QUALITY — CLEAN CHAIN",
            test_23,
        ),
        (
            24,
            "DATA QUALITY — MIXED PRICING",
            test_24,
        ),
        (
            25,
            "ZERO OI SAFETY",
            test_25,
        ),
        (
            26,
            "DUPLICATE CONTRACT SAFETY",
            test_26,
        ),
        (
            27,
            "NO TRADE / ORDER INTENT",
            test_27,
        ),
        (
            28,
            "FINAL AUTHORITY INVARIANT",
            test_28,
        ),
    ]

    for (
        number,
        title,
        function,
    ) in tests:
        run_case(
            number,
            title,
            function,
        )

    heading(
        "OPTION MARKET INTELLIGENCE ENGINE TEST SUMMARY"
    )

    print(
        f"Tests Passed : "
        f"{PASSED_TESTS}/{TOTAL_TESTS}"
    )

    assert_equal(
        PASSED_TESTS,
        TOTAL_TESTS,
        "Not all intelligence tests passed",
    )

    print()
    print(
        "✅ ALL OPTION MARKET INTELLIGENCE "
        f"ENGINE TESTS PASSED "
        f"({PASSED_TESTS}/{TOTAL_TESTS})"
    )
    print(
        "🔒 INTELLIGENCE ENGINE IS ANALYTICAL ONLY"
    )
    print(
        "🔒 NO CONTRACT-SELECTION AUTHORITY"
    )
    print(
        "🔒 NO TRADE-DECISION AUTHORITY"
    )
    print(
        "🔒 NO STRATEGY-SELECTION AUTHORITY"
    )
    print(
        "🔒 NO RISK/POSITION-SIZING AUTHORITY"
    )
    print(
        "🔒 NO BROKER ORDER AUTHORITY"
    )


if __name__ == "__main__":
    run_tests()