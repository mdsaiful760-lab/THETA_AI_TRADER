# ============================================================
# THETA AI TRADER — MARKET REGIME ENGINE TEST SUITE
# ============================================================

from market_regime_engine import MarketRegimeEngine


# ============================================================
# TEST HELPERS
# ============================================================

def print_header(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def print_result(result):
    print("Regime             :", result.get("regime"))
    print("Base Regime        :", result.get("base_regime"))
    print("Confidence         :", result.get("regime_confidence"))
    print("Direction          :", result.get("preferred_direction"))
    print("Trade Permission   :", result.get("trade_permission"))
    print("Entry Allowed      :", result.get("entry_allowed"))
    print("Bullish Score      :", result.get("bullish_score"))
    print("Bearish Score      :", result.get("bearish_score"))
    print("Range Score        :", result.get("range_score"))
    print("Risk Score         :", result.get("risk_score"))
    print("Permission Reasons :", result.get("permission_reasons"))


def assert_equal(
    actual,
    expected,
    message,
):
    if actual != expected:
        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}"
        )


def assert_in(
    actual,
    expected_values,
    message,
):
    if actual not in expected_values:
        raise AssertionError(
            f"{message}\n"
            f"Expected one of: {expected_values}\n"
            f"Actual         : {actual}"
        )


# ============================================================
# COMMON SESSION
# ============================================================

def normal_session():
    return {
        "market_open": True,
        "new_entries_allowed": True,
        "minutes_from_open": 60,
    }


# ============================================================
# TEST 1 — NORMAL BULLISH MARKET
# ============================================================

def test_normal_bullish():

    engine = MarketRegimeEngine()

    oi = {
        "oi_directional_bias": "BULLISH",
        "oi_confidence": "HIGH",
        "chain_structure": "BULLISH_SHIFT",
        "resistance_state": "WEAKENING",
        "support_state": "STRENGTHENING",
        "current_oi_pcr": 1.25,
        "near_atm_pcr": 1.30,
        "major_resistance_strike": 24300,
        "major_support_strike": 24200,
    }

    technical = {
        "spot": 24275,
        "price_trend": "BULLISH",
        "ema_structure": "BULLISH",
        "rsi": 58,
        "vwap_position": "ABOVE",
    }

    volatility = {
        "volatility_state": "NORMAL",
        "spike_detected": False,
        "abnormal_candle": False,
        "rapid_move": False,
    }

    result = engine.analyze(
        oi_analysis=oi,
        technical=technical,
        volatility=volatility,
        session=normal_session(),
        is_expiry_day=False,
    )

    print_header(
        "TEST 1 — NORMAL BULLISH MARKET"
    )

    print_result(result)

    assert_in(
        result["regime"],
        (
            "TRENDING_BULLISH",
            "BREAKOUT_BULLISH",
        ),
        "Bullish market regime classification failed",
    )

    assert_equal(
        result["preferred_direction"],
        "BULLISH",
        "Bullish direction detection failed",
    )

    assert_equal(
        result["trade_permission"],
        "ALLOW",
        "Normal bullish market should allow trading",
    )

    assert_equal(
        result["entry_allowed"],
        True,
        "Normal bullish market should allow entry",
    )

    print("✅ PASS — Normal bullish market")


# ============================================================
# TEST 2 — NORMAL BEARISH MARKET
# ============================================================

def test_normal_bearish():

    engine = MarketRegimeEngine()

    oi = {
        "oi_directional_bias": "BEARISH",
        "oi_confidence": "HIGH",
        "chain_structure": "BEARISH_SHIFT",
        "resistance_state": "STRENGTHENING",
        "support_state": "WEAKENING",
        "current_oi_pcr": 0.78,
        "near_atm_pcr": 0.75,
        "major_resistance_strike": 24300,
        "major_support_strike": 24200,
    }

    technical = {
        "spot": 24240,
        "price_trend": "BEARISH",
        "ema_structure": "BEARISH",
        "rsi": 38,
        "vwap_position": "BELOW",
    }

    volatility = {
        "volatility_state": "NORMAL",
        "spike_detected": False,
        "abnormal_candle": False,
        "rapid_move": False,
    }

    result = engine.analyze(
        oi_analysis=oi,
        technical=technical,
        volatility=volatility,
        session=normal_session(),
        is_expiry_day=False,
    )

    print_header(
        "TEST 2 — NORMAL BEARISH MARKET"
    )

    print_result(result)

    assert_in(
        result["regime"],
        (
            "TRENDING_BEARISH",
            "BREAKDOWN_BEARISH",
        ),
        "Bearish market regime classification failed",
    )

    assert_equal(
        result["preferred_direction"],
        "BEARISH",
        "Bearish direction detection failed",
    )

    assert_equal(
        result["trade_permission"],
        "ALLOW",
        "Normal bearish market should allow trading",
    )

    assert_equal(
        result["entry_allowed"],
        True,
        "Normal bearish market should allow entry",
    )

    print("✅ PASS — Normal bearish market")


# ============================================================
# TEST 3 — RANGE MARKET
# ============================================================

def test_range_market():

    engine = MarketRegimeEngine()

    oi = {
        "oi_directional_bias": "RANGE",
        "oi_confidence": "HIGH",
        "chain_structure": "RANGE_BUILDING",
        "resistance_state": "STRENGTHENING",
        "support_state": "STRENGTHENING",
        "current_oi_pcr": 1.00,
        "near_atm_pcr": 1.02,
        "major_resistance_strike": 24400,
        "major_support_strike": 24200,
    }

    technical = {
        "spot": 24300,
        "price_trend": "SIDEWAYS",
        "ema_structure": "FLAT",
        "rsi": 50,
        "vwap_position": "NEAR",
    }

    volatility = {
        "volatility_state": "NORMAL",
        "spike_detected": False,
        "abnormal_candle": False,
        "rapid_move": False,
    }

    result = engine.analyze(
        oi_analysis=oi,
        technical=technical,
        volatility=volatility,
        session=normal_session(),
        is_expiry_day=False,
    )

    print_header(
        "TEST 3 — RANGE MARKET"
    )

    print_result(result)

    assert_in(
        result["regime"],
        (
            "RANGE_BOUND",
            "MIXED",
        ),
        "Range market classification failed",
    )

    assert_in(
        result["preferred_direction"],
        (
            "NON_DIRECTIONAL",
            "RANGE",
            "NONE",
        ),
        "Range direction detection failed",
    )

    print("✅ PASS — Range market")


# ============================================================
# TEST 4 — BULLISH BREAKOUT
# ============================================================

def test_bullish_breakout():

    engine = MarketRegimeEngine()

    oi = {
        "oi_directional_bias": "BULLISH",
        "oi_confidence": "HIGH",
        "chain_structure": "BULLISH_SHIFT",
        "resistance_state": "WEAKENING",
        "support_state": "STRENGTHENING",
        "current_oi_pcr": 1.30,
        "near_atm_pcr": 1.35,
        "major_resistance_strike": 24300,
        "major_support_strike": 24200,
    }

    technical = {
        "spot": 24325,
        "price_trend": "BULLISH",
        "ema_structure": "BULLISH",
        "rsi": 62,
        "vwap_position": "ABOVE",
    }

    volatility = {
        "volatility_state": "NORMAL",
        "spike_detected": False,
        "abnormal_candle": False,
        "rapid_move": False,
    }

    result = engine.analyze(
        oi_analysis=oi,
        technical=technical,
        volatility=volatility,
        session=normal_session(),
        is_expiry_day=False,
    )

    print_header(
        "TEST 4 — BULLISH BREAKOUT"
    )

    print_result(result)

    assert_equal(
        result["base_regime"],
        "BREAKOUT_BULLISH",
        "Bullish breakout was not detected",
    )

    assert_equal(
        result["preferred_direction"],
        "BULLISH",
        "Bullish breakout direction failed",
    )

    assert_equal(
        result["entry_allowed"],
        True,
        "Safe bullish breakout should allow entry",
    )

    print("✅ PASS — Bullish breakout")


# ============================================================
# TEST 5 — BEARISH BREAKDOWN
# ============================================================

def test_bearish_breakdown():

    engine = MarketRegimeEngine()

    oi = {
        "oi_directional_bias": "BEARISH",
        "oi_confidence": "HIGH",
        "chain_structure": "BEARISH_SHIFT",
        "resistance_state": "STRENGTHENING",
        "support_state": "WEAKENING",
        "current_oi_pcr": 0.75,
        "near_atm_pcr": 0.70,
        "major_resistance_strike": 24300,
        "major_support_strike": 24200,
    }

    technical = {
        "spot": 24175,
        "price_trend": "BEARISH",
        "ema_structure": "BEARISH",
        "rsi": 35,
        "vwap_position": "BELOW",
    }

    volatility = {
        "volatility_state": "NORMAL",
        "spike_detected": False,
        "abnormal_candle": False,
        "rapid_move": False,
    }

    result = engine.analyze(
        oi_analysis=oi,
        technical=technical,
        volatility=volatility,
        session=normal_session(),
        is_expiry_day=False,
    )

    print_header(
        "TEST 5 — BEARISH BREAKDOWN"
    )

    print_result(result)

    assert_equal(
        result["base_regime"],
        "BREAKOUT_BEARISH",
        "Bearish breakout regime was not detected",
    )

    assert_equal(
        result["preferred_direction"],
        "BEARISH",
        "Bearish breakdown direction failed",
    )

    assert_equal(
        result["entry_allowed"],
        True,
        "Safe bearish breakdown should allow entry",
    )

    print("✅ PASS — Bearish breakdown")


# ============================================================
# TEST 6 — EXPIRY-DAY SPIKE PROTECTION
# ============================================================

def test_expiry_spike():

    engine = MarketRegimeEngine()

    oi = {
        "oi_directional_bias": "BULLISH",
        "oi_confidence": "HIGH",
        "chain_structure": "BULLISH_SHIFT",
        "resistance_state": "WEAKENING",
        "support_state": "STRENGTHENING",
        "current_oi_pcr": 1.30,
        "near_atm_pcr": 1.35,
        "major_resistance_strike": 24300,
        "major_support_strike": 24200,
    }

    technical = {
        "spot": 24320,
        "price_trend": "BULLISH",
        "ema_structure": "BULLISH",
        "rsi": 64,
        "vwap_position": "ABOVE",
    }

    volatility = {
        "volatility_state": "HIGH",
        "spike_detected": True,
        "abnormal_candle": True,
        "rapid_move": True,
    }

    result = engine.analyze(
        oi_analysis=oi,
        technical=technical,
        volatility=volatility,
        session=normal_session(),
        is_expiry_day=True,
    )

    print_header(
        "TEST 6 — EXPIRY-DAY SPIKE PROTECTION"
    )

    print_result(result)

    assert_equal(
        result["regime"],
        "EXPIRY_SPIKE_RISK",
        "Expiry spike regime protection failed",
    )

    assert_equal(
        result["preferred_direction"],
        "NONE",
        "Direction must be disabled during expiry spike",
    )

    assert_equal(
        result["trade_permission"],
        "BLOCK",
        "Expiry spike must block new trades",
    )

    assert_equal(
        result["entry_allowed"],
        False,
        "Expiry spike must prevent entry",
    )

    print("✅ PASS — Expiry spike blocked")


# ============================================================
# TEST 7 — NON-EXPIRY ACTIVE SPIKE
# ============================================================

def test_non_expiry_spike():

    engine = MarketRegimeEngine()

    oi = {
        "oi_directional_bias": "BEARISH",
        "oi_confidence": "HIGH",
        "chain_structure": "BEARISH_SHIFT",
        "resistance_state": "STRENGTHENING",
        "support_state": "WEAKENING",
        "current_oi_pcr": 0.80,
        "near_atm_pcr": 0.78,
        "major_resistance_strike": 24300,
        "major_support_strike": 24200,
    }

    technical = {
        "spot": 24180,
        "price_trend": "BEARISH",
        "ema_structure": "BEARISH",
        "rsi": 32,
        "vwap_position": "BELOW",
    }

    volatility = {
        "volatility_state": "HIGH",
        "spike_detected": True,
        "abnormal_candle": True,
        "rapid_move": True,
    }

    result = engine.analyze(
        oi_analysis=oi,
        technical=technical,
        volatility=volatility,
        session=normal_session(),
        is_expiry_day=False,
    )

    print_header(
        "TEST 7 — NON-EXPIRY ACTIVE SPIKE"
    )

    print_result(result)

    assert_equal(
        result["trade_permission"],
        "BLOCK",
        "Active spike should block new entries",
    )

    assert_equal(
        result["entry_allowed"],
        False,
        "Active spike should prevent entry",
    )

    print("✅ PASS — Active spike blocked")


# ============================================================
# TEST 8 — SESSION BLOCK
# ============================================================

def test_session_block():

    engine = MarketRegimeEngine()

    oi = {
        "oi_directional_bias": "BULLISH",
        "oi_confidence": "HIGH",
        "chain_structure": "BULLISH_SHIFT",
        "resistance_state": "WEAKENING",
        "support_state": "STRENGTHENING",
        "current_oi_pcr": 1.25,
        "near_atm_pcr": 1.25,
        "major_resistance_strike": 24300,
        "major_support_strike": 24200,
    }

    technical = {
        "spot": 24275,
        "price_trend": "BULLISH",
        "ema_structure": "BULLISH",
        "rsi": 58,
        "vwap_position": "ABOVE",
    }

    volatility = {
        "volatility_state": "NORMAL",
        "spike_detected": False,
        "abnormal_candle": False,
        "rapid_move": False,
    }

    session = {
        "market_open": True,
        "new_entries_allowed": False,
        "minutes_from_open": 360,
    }

    result = engine.analyze(
        oi_analysis=oi,
        technical=technical,
        volatility=volatility,
        session=session,
        is_expiry_day=False,
    )

    print_header(
        "TEST 8 — SESSION ENTRY BLOCK"
    )

    print_result(result)

    assert_equal(
        result["trade_permission"],
        "BLOCK",
        "Session restriction should block entry",
    )

    assert_equal(
        result["entry_allowed"],
        False,
        "Session restriction should prevent entry",
    )

    print("✅ PASS — Session restriction blocked entry")


# ============================================================
# TEST 9 — CONFLICTING SIGNALS
# ============================================================

def test_conflicting_signals():

    engine = MarketRegimeEngine()

    oi = {
        "oi_directional_bias": "BULLISH",
        "oi_confidence": "HIGH",
        "chain_structure": "BULLISH_SHIFT",
        "resistance_state": "WEAKENING",
        "support_state": "STRENGTHENING",
        "current_oi_pcr": 1.25,
        "near_atm_pcr": 1.30,
        "major_resistance_strike": 24300,
        "major_support_strike": 24200,
    }

    # Technical structure deliberately contradicts OI.
    technical = {
        "spot": 24250,
        "price_trend": "BEARISH",
        "ema_structure": "BEARISH",
        "rsi": 42,
        "vwap_position": "BELOW",
    }

    volatility = {
        "volatility_state": "NORMAL",
        "spike_detected": False,
        "abnormal_candle": False,
        "rapid_move": False,
    }

    result = engine.analyze(
        oi_analysis=oi,
        technical=technical,
        volatility=volatility,
        session=normal_session(),
        is_expiry_day=False,
    )

    print_header(
        "TEST 9 — CONFLICTING OI + TECHNICAL SIGNALS"
    )

    print_result(result)

    # We deliberately do not force a particular regime here.
    # Different scoring implementations can classify this as
    # MIXED or one side with LOW confidence.
    #
    # What matters is that the engine does not confidently
    # allow an unsafe directional trade.

    if (
        result["trade_permission"] == "ALLOW"
        and result["regime_confidence"] == "HIGH"
    ):
        raise AssertionError(
            "Conflicting OI and technical evidence "
            "must not produce HIGH-confidence ALLOW"
        )

    print(
        "✅ PASS — Conflicting evidence handled conservatively"
    )


# ============================================================
# RUN ALL TESTS
# ============================================================

def run_all_tests():

    print()
    print("=" * 78)
    print("🧪 THETA AI TRADER — MARKET REGIME ENGINE TEST SUITE")
    print("=" * 78)

    tests = [
        test_normal_bullish,
        test_normal_bearish,
        test_range_market,
        test_bullish_breakout,
        test_bearish_breakdown,
        test_expiry_spike,
        test_non_expiry_spike,
        test_session_block,
        test_conflicting_signals,
    ]

    passed = 0

    for test in tests:

        try:
            test()
            passed += 1

        except Exception as error:

            print()
            print("❌ TEST FAILED")
            print("Test :", test.__name__)
            print("Error:", error)

            print()
            print("=" * 78)
            print(
                f"❌ MARKET REGIME TESTS FAILED "
                f"({passed}/{len(tests)} passed)"
            )
            print("=" * 78)

            raise

    print()
    print("=" * 78)
    print(
        f"✅ ALL MARKET REGIME ENGINE TESTS PASSED "
        f"({passed}/{len(tests)})"
    )
    print("🔒 TEST ONLY — NO ORDER PLACEMENT")
    print("=" * 78)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    run_all_tests()