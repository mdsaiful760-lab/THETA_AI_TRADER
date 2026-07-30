# ============================================================
# THETA AI TRADER — SIGNAL DECISION ENGINE TEST SUITE
# ============================================================

from signal_decision_engine import SignalDecisionEngine


# ============================================================
# TEST HELPERS
# ============================================================

def print_header(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def show_result(result):
    print("Decision            :", result["decision"])
    print("Setup Valid         :", result["setup_valid"])
    print("Direction           :", result["direction"])
    print("Confidence          :", result["confidence"])
    print("Trade Permission    :", result["trade_permission"])
    print("Regime              :", result["regime"])
    print("Base Regime         :", result["base_regime"])
    print(
        "Bull Confirmation   :",
        result.get("bullish_confirmation"),
    )
    print(
        "Bear Confirmation   :",
        result.get("bearish_confirmation"),
    )
    print(
        "Required Confirm    :",
        result.get("required_confirmation"),
    )
    print(
        "Signal Conflict     :",
        result.get("signal_conflict"),
    )
    print("Reasons             :", result["reasons"])
    print("Safety Flags        :", result["safety_flags"])


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


def assert_true(
    value,
    message,
):
    if not value:
        raise AssertionError(message)


def assert_false(
    value,
    message,
):
    if value:
        raise AssertionError(message)


def assert_in(
    value,
    expected_values,
    message,
):
    if value not in expected_values:
        raise AssertionError(
            f"{message}\n"
            f"Expected one of: {expected_values}\n"
            f"Actual         : {value}"
        )


# ============================================================
# TEST 1 — NORMAL BULLISH SETUP
# ============================================================

def test_normal_bullish():

    print_header(
        "TEST 1 — NORMAL BULLISH SETUP"
    )

    engine = SignalDecisionEngine()

    regime = {
        "regime": "TRENDING_BULLISH",
        "base_regime": "TRENDING_BULLISH",
        "regime_confidence": "HIGH",
        "preferred_direction": "BULLISH",
        "trade_permission": "ALLOW",
        "entry_allowed": True,
        "signal_conflict": False,
    }

    oi = {
        "oi_directional_bias": "BULLISH",
    }

    technical = {
        "price_trend": "BULLISH",
        "ema_structure": "BULLISH",
        "vwap_position": "ABOVE",
    }

    result = engine.analyze(
        regime_analysis=regime,
        oi_analysis=oi,
        technical=technical,
    )

    show_result(result)

    assert_equal(
        result["decision"],
        "BULLISH_SETUP",
        "Bullish setup was not detected",
    )

    assert_true(
        result["setup_valid"],
        "Bullish setup should be valid",
    )

    assert_equal(
        result["direction"],
        "BULLISH",
        "Bullish direction incorrect",
    )

    print(
        "✅ PASS — Normal bullish setup"
    )


# ============================================================
# TEST 2 — NORMAL BEARISH SETUP
# ============================================================

def test_normal_bearish():

    print_header(
        "TEST 2 — NORMAL BEARISH SETUP"
    )

    engine = SignalDecisionEngine()

    regime = {
        "regime": "TRENDING_BEARISH",
        "base_regime": "TRENDING_BEARISH",
        "regime_confidence": "HIGH",
        "preferred_direction": "BEARISH",
        "trade_permission": "ALLOW",
        "entry_allowed": True,
        "signal_conflict": False,
    }

    oi = {
        "oi_directional_bias": "BEARISH",
    }

    technical = {
        "price_trend": "BEARISH",
        "ema_structure": "BEARISH",
        "vwap_position": "BELOW",
    }

    result = engine.analyze(
        regime_analysis=regime,
        oi_analysis=oi,
        technical=technical,
    )

    show_result(result)

    assert_equal(
        result["decision"],
        "BEARISH_SETUP",
        "Bearish setup was not detected",
    )

    assert_true(
        result["setup_valid"],
        "Bearish setup should be valid",
    )

    assert_equal(
        result["direction"],
        "BEARISH",
        "Bearish direction incorrect",
    )

    print(
        "✅ PASS — Normal bearish setup"
    )


# ============================================================
# TEST 3 — BULLISH BREAKOUT
# ============================================================

def test_bullish_breakout():

    print_header(
        "TEST 3 — BULLISH BREAKOUT SETUP"
    )

    engine = SignalDecisionEngine()

    regime = {
        "regime": "BREAKOUT_BULLISH",
        "base_regime": "BREAKOUT_BULLISH",
        "regime_confidence": "HIGH",
        "preferred_direction": "BULLISH",
        "trade_permission": "ALLOW",
        "entry_allowed": True,
        "signal_conflict": False,
    }

    oi = {
        "oi_directional_bias": "BULLISH",
    }

    technical = {
        "price_trend": "BULLISH",
        "ema_structure": "BULLISH",
        "vwap_position": "ABOVE",
    }

    result = engine.analyze(
        regime_analysis=regime,
        oi_analysis=oi,
        technical=technical,
    )

    show_result(result)

    assert_equal(
        result["decision"],
        "BREAKOUT_SETUP",
        "Bullish breakout was not detected",
    )

    assert_equal(
        result["direction"],
        "BULLISH",
        "Bullish breakout direction incorrect",
    )

    assert_true(
        result["setup_valid"],
        "Bullish breakout should be valid",
    )

    print(
        "✅ PASS — Bullish breakout setup"
    )


# ============================================================
# TEST 4 — BEARISH BREAKOUT / BREAKDOWN
# ============================================================

def test_bearish_breakout():

    print_header(
        "TEST 4 — BEARISH BREAKOUT SETUP"
    )

    engine = SignalDecisionEngine()

    regime = {
        "regime": "BREAKOUT_BEARISH",
        "base_regime": "BREAKOUT_BEARISH",
        "regime_confidence": "HIGH",
        "preferred_direction": "BEARISH",
        "trade_permission": "ALLOW",
        "entry_allowed": True,
        "signal_conflict": False,
    }

    oi = {
        "oi_directional_bias": "BEARISH",
    }

    technical = {
        "price_trend": "BEARISH",
        "ema_structure": "BEARISH",
        "vwap_position": "BELOW",
    }

    result = engine.analyze(
        regime_analysis=regime,
        oi_analysis=oi,
        technical=technical,
    )

    show_result(result)

    assert_equal(
        result["decision"],
        "BREAKOUT_SETUP",
        "Bearish breakout was not detected",
    )

    assert_equal(
        result["direction"],
        "BEARISH",
        "Bearish breakout direction incorrect",
    )

    assert_true(
        result["setup_valid"],
        "Bearish breakout should be valid",
    )

    print(
        "✅ PASS — Bearish breakout setup"
    )


# ============================================================
# TEST 5 — RANGE SETUP
# ============================================================

def test_range_setup():

    print_header(
        "TEST 5 — RANGE SETUP"
    )

    engine = SignalDecisionEngine()

    regime = {
        "regime": "RANGE_BOUND",
        "base_regime": "RANGE_BOUND",
        "regime_confidence": "HIGH",
        "preferred_direction": "NON_DIRECTIONAL",
        "trade_permission": "ALLOW",
        "entry_allowed": True,
        "signal_conflict": False,
    }

    oi = {
        "oi_directional_bias": "RANGE",
    }

    technical = {
        "price_trend": "SIDEWAYS",
        "ema_structure": "FLAT",
        "vwap_position": "NEAR",
    }

    result = engine.analyze(
        regime_analysis=regime,
        oi_analysis=oi,
        technical=technical,
    )

    show_result(result)

    assert_equal(
        result["decision"],
        "RANGE_SETUP",
        "Range setup was not detected",
    )

    assert_true(
        result["setup_valid"],
        "Range setup should be valid",
    )

    assert_equal(
        result["direction"],
        "NON_DIRECTIONAL",
        "Range direction incorrect",
    )

    print(
        "✅ PASS — Range setup"
    )


# ============================================================
# TEST 6 — LOW CONFIDENCE
# ============================================================

def test_low_confidence():

    print_header(
        "TEST 6 — LOW CONFIDENCE WAIT"
    )

    engine = SignalDecisionEngine()

    regime = {
        "regime": "TRENDING_BULLISH",
        "base_regime": "TRENDING_BULLISH",
        "regime_confidence": "LOW",
        "preferred_direction": "BULLISH",
        "trade_permission": "ALLOW",
        "entry_allowed": True,
        "signal_conflict": False,
    }

    oi = {
        "oi_directional_bias": "BULLISH",
    }

    technical = {
        "price_trend": "BULLISH",
        "ema_structure": "BULLISH",
        "vwap_position": "ABOVE",
    }

    result = engine.analyze(
        regime_analysis=regime,
        oi_analysis=oi,
        technical=technical,
    )

    show_result(result)

    assert_equal(
        result["decision"],
        "WAIT",
        "Low confidence should produce WAIT",
    )

    assert_false(
        result["setup_valid"],
        "Low-confidence setup must not be valid",
    )

    print(
        "✅ PASS — Low confidence waits"
    )


# ============================================================
# TEST 7 — REGIME HARD BLOCK
# ============================================================

def test_regime_block():

    print_header(
        "TEST 7 — REGIME HARD BLOCK"
    )

    engine = SignalDecisionEngine()

    regime = {
        "regime": "UNSTABLE",
        "base_regime": "TRENDING_BULLISH",
        "regime_confidence": "HIGH",
        "preferred_direction": "NONE",
        "trade_permission": "BLOCK",
        "entry_allowed": False,
        "signal_conflict": False,
    }

    result = engine.analyze(
        regime_analysis=regime,
    )

    show_result(result)

    assert_equal(
        result["decision"],
        "NO_TRADE",
        "Blocked regime must produce NO_TRADE",
    )

    assert_false(
        result["setup_valid"],
        "Blocked regime cannot create setup",
    )

    assert_in(
        "REGIME_PERMISSION_BLOCK",
        result["safety_flags"],
        "Permission block safety flag missing",
    )

    print(
        "✅ PASS — Regime hard block"
    )


# ============================================================
# TEST 8 — EXPIRY SPIKE
# ============================================================

def test_expiry_spike():

    print_header(
        "TEST 8 — EXPIRY SPIKE BLOCK"
    )

    engine = SignalDecisionEngine()

    regime = {
        "regime": "EXPIRY_SPIKE_RISK",
        "base_regime": "BREAKOUT_BULLISH",
        "regime_confidence": "HIGH",
        "preferred_direction": "NONE",
        "trade_permission": "BLOCK",
        "entry_allowed": False,
        "signal_conflict": False,
    }

    volatility = {
        "spike_detected": True,
        "abnormal_candle": True,
        "rapid_move": True,
    }

    result = engine.analyze(
        regime_analysis=regime,
        volatility=volatility,
    )

    show_result(result)

    assert_equal(
        result["decision"],
        "NO_TRADE",
        "Expiry spike must produce NO_TRADE",
    )

    assert_false(
        result["setup_valid"],
        "Expiry spike cannot create setup",
    )

    assert_in(
        "ACTIVE_PRICE_SPIKE",
        result["safety_flags"],
        "Spike safety flag missing",
    )

    print(
        "✅ PASS — Expiry spike blocked"
    )


# ============================================================
# TEST 9 — RAPID MOVE
# ============================================================

def test_rapid_move():

    print_header(
        "TEST 9 — RAPID MOVE BLOCK"
    )

    engine = SignalDecisionEngine()

    regime = {
        "regime": "TRENDING_BULLISH",
        "base_regime": "TRENDING_BULLISH",
        "regime_confidence": "HIGH",
        "preferred_direction": "BULLISH",
        "trade_permission": "ALLOW",
        "entry_allowed": True,
        "signal_conflict": False,
    }

    oi = {
        "oi_directional_bias": "BULLISH",
    }

    technical = {
        "price_trend": "BULLISH",
        "ema_structure": "BULLISH",
        "vwap_position": "ABOVE",
    }

    volatility = {
        "rapid_move": True,
    }

    result = engine.analyze(
        regime_analysis=regime,
        oi_analysis=oi,
        technical=technical,
        volatility=volatility,
    )

    show_result(result)

    assert_equal(
        result["decision"],
        "NO_TRADE",
        "Rapid move must block new setup",
    )

    assert_in(
        "RAPID_MOVE",
        result["safety_flags"],
        "Rapid move safety flag missing",
    )

    print(
        "✅ PASS — Rapid move blocked"
    )


# ============================================================
# TEST 10 — SESSION ENTRY BLOCK
# ============================================================

def test_session_block():

    print_header(
        "TEST 10 — SESSION ENTRY BLOCK"
    )

    engine = SignalDecisionEngine()

    regime = {
        "regime": "TRENDING_BULLISH",
        "base_regime": "TRENDING_BULLISH",
        "regime_confidence": "HIGH",
        "preferred_direction": "BULLISH",
        "trade_permission": "ALLOW",
        "entry_allowed": True,
        "signal_conflict": False,
    }

    oi = {
        "oi_directional_bias": "BULLISH",
    }

    technical = {
        "price_trend": "BULLISH",
        "ema_structure": "BULLISH",
        "vwap_position": "ABOVE",
    }

    session = {
        "market_open": True,
        "new_entries_allowed": False,
    }

    result = engine.analyze(
        regime_analysis=regime,
        oi_analysis=oi,
        technical=technical,
        session=session,
    )

    show_result(result)

    assert_equal(
        result["decision"],
        "NO_TRADE",
        "Session block must produce NO_TRADE",
    )

    assert_in(
        "SESSION_ENTRY_BLOCK",
        result["safety_flags"],
        "Session safety flag missing",
    )

    print(
        "✅ PASS — Session entry blocked"
    )


# ============================================================
# TEST 11 — OI / TECHNICAL CONFLICT UNDER CAUTION
# ============================================================

def test_signal_conflict():

    print_header(
        "TEST 11 — OI / TECHNICAL CONFLICT"
    )

    engine = SignalDecisionEngine()

    regime = {
        "regime": "TRENDING_BULLISH",
        "base_regime": "TRENDING_BULLISH",
        "regime_confidence": "MEDIUM",
        "preferred_direction": "BULLISH",
        "trade_permission": "CAUTION",
        "entry_allowed": True,
        "signal_conflict": True,
    }

    # OI bullish, technicals bearish.
    oi = {
        "oi_directional_bias": "BULLISH",
    }

    technical = {
        "price_trend": "BEARISH",
        "ema_structure": "BEARISH",
        "vwap_position": "BELOW",
    }

    result = engine.analyze(
        regime_analysis=regime,
        oi_analysis=oi,
        technical=technical,
    )

    show_result(result)

    assert_equal(
        result["decision"],
        "WAIT",
        (
            "Conflicting OI/technical evidence "
            "must produce WAIT"
        ),
    )

    assert_false(
        result["setup_valid"],
        (
            "Conflicting evidence must not "
            "create a valid setup"
        ),
    )

    assert_equal(
        result["direction"],
        "NONE",
        (
            "Conflicting evidence must not "
            "select trade direction"
        ),
    )

    assert_true(
        result["signal_conflict"],
        "Signal conflict flag missing",
    )

    print(
        "✅ PASS — Conflict waits for confirmation"
    )


# ============================================================
# TEST 12 — CAUTION WITH FULL AGREEMENT
# ============================================================

def test_caution_full_agreement():

    print_header(
        "TEST 12 — CAUTION WITH FULL AGREEMENT"
    )

    engine = SignalDecisionEngine()

    regime = {
        "regime": "TRENDING_BULLISH",
        "base_regime": "TRENDING_BULLISH",
        "regime_confidence": "MEDIUM",
        "preferred_direction": "BULLISH",
        "trade_permission": "CAUTION",
        "entry_allowed": True,
        "signal_conflict": False,
    }

    oi = {
        "oi_directional_bias": "BULLISH",
    }

    technical = {
        "price_trend": "BULLISH",
        "ema_structure": "BULLISH",
        "vwap_position": "ABOVE",
    }

    result = engine.analyze(
        regime_analysis=regime,
        oi_analysis=oi,
        technical=technical,
    )

    show_result(result)

    assert_equal(
        result["decision"],
        "BULLISH_SETUP",
        (
            "CAUTION with full agreement "
            "should allow bullish setup"
        ),
    )

    assert_true(
        result["setup_valid"],
        (
            "Fully confirmed CAUTION setup "
            "should be valid"
        ),
    )

    assert_true(
        result["confirmation_score"]
        >= result["required_confirmation"],
        "Confirmation requirement not satisfied",
    )

    print(
        "✅ PASS — CAUTION accepted only "
        "with strong agreement"
    )


# ============================================================
# RUN ALL TESTS
# ============================================================

def run_all_tests():

    print()
    print("=" * 78)
    print(
        "🧪 THETA AI TRADER — "
        "SIGNAL DECISION ENGINE TEST SUITE"
    )
    print("=" * 78)

    tests = [
        test_normal_bullish,
        test_normal_bearish,
        test_bullish_breakout,
        test_bearish_breakout,
        test_range_setup,
        test_low_confidence,
        test_regime_block,
        test_expiry_spike,
        test_rapid_move,
        test_session_block,
        test_signal_conflict,
        test_caution_full_agreement,
    ]

    passed = 0

    for test in tests:

        try:

            test()

            passed += 1

        except Exception as error:

            print()
            print("❌ TEST FAILED")
            print(
                "Test :",
                test.__name__,
            )
            print(
                "Error:",
                error,
            )

            print()
            print("=" * 78)
            print(
                "❌ SIGNAL DECISION TESTS FAILED "
                f"({passed}/{len(tests)} passed)"
            )
            print("=" * 78)

            raise

    print()
    print("=" * 78)
    print(
        "✅ ALL SIGNAL DECISION ENGINE "
        f"TESTS PASSED ({passed}/{len(tests)})"
    )
    print(
        "🔒 TEST ONLY — NO ORDER PLACEMENT"
    )
    print("=" * 78)


if __name__ == "__main__":
    run_all_tests()