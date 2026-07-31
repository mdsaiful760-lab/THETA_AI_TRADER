# ============================================================
# THETA AI TRADER — RISK BUDGET ALLOCATOR TEST SUITE
# ============================================================

from risk_budget_allocator import RiskBudgetAllocator


PASSED = 0
TOTAL = 0


def separator(char="=", length=78):
    print(char * length)


def heading(title):
    print()
    separator()
    print(title)
    separator()


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}"
        )


def assert_close(
    actual,
    expected,
    message,
    tolerance=0.02,
):
    if abs(
        float(actual)
        - float(expected)
    ) > tolerance:
        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}"
        )


def assert_true(value, message):
    if value is not True:
        raise AssertionError(
            f"{message}\n"
            f"Expected: True\n"
            f"Actual  : {value}"
        )


def assert_false(value, message):
    if value is not False:
        raise AssertionError(
            f"{message}\n"
            f"Expected: False\n"
            f"Actual  : {value}"
        )


def print_result(result):

    print(
        "Allocation Mode       :",
        result["allocation_mode"],
    )

    print(
        "Permission            :",
        result["allocation_permission"],
    )

    print(
        "Allocation Allowed    :",
        result["allocation_allowed"],
    )

    print(
        "Reason                :",
        result["reason"],
    )

    print(
        "Daily Risk Budget     :",
        result["daily_risk_budget_rupees"],
    )

    print(
        "Remaining Daily Risk  :",
        result["remaining_daily_risk_rupees"],
    )

    print(
        "Trades Taken          :",
        result["trades_taken_today"],
    )

    print(
        "Trades Remaining      :",
        result["trades_remaining"],
    )

    print(
        "Setup Score           :",
        result["setup_score"],
    )

    print(
        "Confidence Multiplier :",
        result["confidence_multiplier"],
    )

    print(
        "Base Risk Allocation  :",
        result["base_risk_allocation_rupees"],
    )

    print(
        "Adjusted Risk         :",
        result["confidence_adjusted_risk_rupees"],
    )

    print(
        "Single Trade Cap      :",
        result["single_trade_cap_rupees"],
    )

    print(
        "APPROVED RISK         :",
        result["approved_risk_rupees"],
    )


# ============================================================
# TEST 1
# FIXED MODE — THREE TRADE ALLOCATION
# ============================================================

def test_fixed_three_trade_allocation():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="FIXED",
        max_trades_per_day=3,
        confidence_scaling_enabled=False,
        max_single_trade_daily_risk_pct=100,
    )

    result = allocator.allocate(
        daily_risk_budget_rupees=10000,
        remaining_daily_risk_rupees=10000,
        trades_taken_today=0,
        setup_score=80,
    )

    print_result(result)

    assert_true(
        result["allocation_allowed"],
        "Fixed allocation should be allowed",
    )

    assert_close(
        result["approved_risk_rupees"],
        3333.33,
        "₹10,000 / 3 trades should allocate "
        "about ₹3,333.33",
    )

    print(
        "✅ PASS — Fixed risk divided across "
        "three trades"
    )


# ============================================================
# TEST 2
# FIXED MODE — ACTUAL REMAINING RISK
# ============================================================

def test_fixed_actual_remaining_budget():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="FIXED",
        max_trades_per_day=3,
        confidence_scaling_enabled=False,
        max_single_trade_daily_risk_pct=100,
    )

    # Trade 1 actual realized loss = ₹1,500.
    #
    # Daily risk budget = ₹10,000
    # Remaining risk    = ₹8,500
    # Remaining trades  = 2
    #
    # ₹8,500 / 2 = ₹4,250

    result = allocator.allocate(
        daily_risk_budget_rupees=10000,
        remaining_daily_risk_rupees=8500,
        trades_taken_today=1,
        setup_score=80,
    )

    print_result(result)

    assert_close(
        result["approved_risk_rupees"],
        4250.00,
        "Allocator must use actual remaining risk",
    )

    print(
        "✅ PASS — Actual remaining risk used correctly"
    )


# ============================================================
# TEST 3
# LOW CONFIDENCE MUST BLOCK
# ============================================================

def test_low_confidence_block():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="INTELLIGENT",
        minimum_setup_score=60,
    )

    result = allocator.allocate(
        daily_risk_budget_rupees=50000,
        remaining_daily_risk_rupees=50000,
        trades_taken_today=0,
        setup_score=55,
    )

    print_result(result)

    assert_false(
        result["allocation_allowed"],
        "Low-confidence setup must not receive risk",
    )

    assert_equal(
        result["reason"],
        "SETUP_SCORE_BELOW_MINIMUM",
        "Wrong low-confidence block reason",
    )

    assert_equal(
        result["approved_risk_rupees"],
        0.0,
        "Blocked setup must receive zero risk",
    )

    print(
        "✅ PASS — Weak setup receives no risk"
    )


# ============================================================
# TEST 4
# INTELLIGENT MODE — MEDIUM SETUP
# ============================================================

def test_intelligent_medium_setup():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="INTELLIGENT",
        max_trades_per_day=3,
        intelligent_reference_trades=3,
        max_single_trade_daily_risk_pct=40,
    )

    result = allocator.allocate(
        daily_risk_budget_rupees=50000,
        remaining_daily_risk_rupees=50000,
        trades_taken_today=0,
        setup_score=80,
    )

    print_result(result)

    # ₹50,000 / 3 = ₹16,666.67
    #
    # Score 80 -> 0.75x
    #
    # Approved = ₹12,500

    assert_close(
        result["confidence_multiplier"],
        0.75,
        "Score 80 should use 0.75 multiplier",
    )

    assert_close(
        result["approved_risk_rupees"],
        12500.00,
        "Medium setup allocation incorrect",
    )

    print(
        "✅ PASS — Medium setup receives "
        "reduced allocation"
    )


# ============================================================
# TEST 5
# INTELLIGENT MODE — STRONG SETUP
# ============================================================

def test_intelligent_strong_setup():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="INTELLIGENT",
        intelligent_reference_trades=3,
        max_single_trade_daily_risk_pct=40,
    )

    result = allocator.allocate(
        daily_risk_budget_rupees=50000,
        remaining_daily_risk_rupees=50000,
        trades_taken_today=0,
        setup_score=92,
    )

    print_result(result)

    # ₹50,000 / 3 = ₹16,666.67
    #
    # Score 92 -> 1.00x

    assert_close(
        result["confidence_multiplier"],
        1.00,
        "Score 92 should receive normal allocation",
    )

    assert_close(
        result["approved_risk_rupees"],
        16666.67,
        "Strong setup allocation incorrect",
    )

    print(
        "✅ PASS — Strong setup receives "
        "normal allocation"
    )


# ============================================================
# TEST 6
# 95+ SETUP MAY RECEIVE INCREASED RISK
# ============================================================

def test_intelligent_very_high_confidence():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="INTELLIGENT",
        intelligent_reference_trades=3,
        max_single_trade_daily_risk_pct=50,
    )

    result = allocator.allocate(
        daily_risk_budget_rupees=50000,
        remaining_daily_risk_rupees=50000,
        trades_taken_today=0,
        setup_score=97,
    )

    print_result(result)

    # Base:
    #
    # ₹50,000 / 3 = ₹16,666.67
    #
    # Score 97 -> 1.25x
    #
    # Approved ≈ ₹20,833.33

    assert_close(
        result["confidence_multiplier"],
        1.25,
        "95+ score should use 1.25 multiplier",
    )

    assert_close(
        result["approved_risk_rupees"],
        20833.33,
        "High-confidence allocation incorrect",
    )

    print(
        "✅ PASS — Exceptional setup receives "
        "controlled increased allocation"
    )


# ============================================================
# TEST 7
# SINGLE TRADE CAP MUST OVERRIDE CONFIDENCE
# ============================================================

def test_single_trade_cap():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="INTELLIGENT",
        intelligent_reference_trades=3,
        max_single_trade_daily_risk_pct=20,
    )

    result = allocator.allocate(
        daily_risk_budget_rupees=50000,
        remaining_daily_risk_rupees=50000,
        trades_taken_today=0,
        setup_score=99,
    )

    print_result(result)

    # Confidence-adjusted allocation wants
    # approximately ₹20,833.
    #
    # But:
    #
    # 20% of ₹50,000 = ₹10,000
    #
    # Therefore safety cap must win.

    assert_close(
        result["approved_risk_rupees"],
        10000.00,
        "Single-trade risk cap must "
        "override confidence",
    )

    print(
        "✅ PASS — Confidence cannot bypass "
        "single-trade cap"
    )


# ============================================================
# TEST 8
# REMAINING DAILY RISK MUST OVERRIDE EVERYTHING
# ============================================================

def test_remaining_daily_budget_protection():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="INTELLIGENT",
        intelligent_reference_trades=3,
        max_single_trade_daily_risk_pct=100,
    )

    result = allocator.allocate(
        daily_risk_budget_rupees=50000,

        # Only ₹7,000 remains.
        remaining_daily_risk_rupees=7000,

        trades_taken_today=2,
        setup_score=100,
    )

    print_result(result)

    assert_close(
        result["approved_risk_rupees"],
        7000.00,
        "Remaining daily budget must be "
        "absolute ceiling",
    )

    assert_true(
        result["approved_risk_rupees"]
        <= result["remaining_daily_risk_rupees"],
        "Approved risk exceeded remaining "
        "daily budget",
    )

    print(
        "✅ PASS — Remaining daily risk overrides "
        "100-score setup"
    )


# ============================================================
# TEST 9
# MAXIMUM TRADE ATTEMPTS
# ============================================================

def test_max_trades_block():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="FIXED",
        max_trades_per_day=3,
    )

    result = allocator.allocate(
        daily_risk_budget_rupees=10000,
        remaining_daily_risk_rupees=10000,
        trades_taken_today=3,
        setup_score=100,
    )

    print_result(result)

    assert_false(
        result["allocation_allowed"],
        "Fourth trade must be blocked",
    )

    assert_equal(
        result["reason"],
        "MAX_TRADES_PER_DAY_REACHED",
        "Wrong maximum-trades block reason",
    )

    assert_equal(
        result["approved_risk_rupees"],
        0.0,
        "Blocked fourth trade must receive zero risk",
    )

    print(
        "✅ PASS — Maximum trade-attempt "
        "limit enforced"
    )


# ============================================================
# TEST 10
# DAILY RISK EXHAUSTED
# ============================================================

def test_daily_budget_exhausted():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="INTELLIGENT",
    )

    result = allocator.allocate(
        daily_risk_budget_rupees=50000,
        remaining_daily_risk_rupees=0,
        trades_taken_today=2,
        setup_score=100,
    )

    print_result(result)

    assert_false(
        result["allocation_allowed"],
        "Trading must stop when daily risk "
        "is exhausted",
    )

    assert_equal(
        result["reason"],
        "DAILY_RISK_BUDGET_EXHAUSTED",
        "Wrong daily-risk block reason",
    )

    print(
        "✅ PASS — Daily risk exhaustion "
        "blocks new trades"
    )


# ============================================================
# TEST 11
# UPSTREAM RISK BLOCK
# ============================================================

def test_upstream_risk_block():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="INTELLIGENT",
    )

    result = allocator.allocate(
        daily_risk_budget_rupees=50000,
        remaining_daily_risk_rupees=50000,
        trades_taken_today=0,
        setup_score=100,

        risk_permission="BLOCK",
        entry_allowed=False,

        hard_blocks=[
            "DAILY_LOSS_LIMIT_REACHED"
        ],
    )

    print_result(result)

    assert_false(
        result["allocation_allowed"],
        "Upstream hard block must stop allocation",
    )

    assert_equal(
        result["reason"],
        "UPSTREAM_RISK_BLOCK",
        "Wrong upstream-block reason",
    )

    assert_equal(
        result["approved_risk_rupees"],
        0.0,
        "Hard-blocked setup received risk",
    )

    print(
        "✅ PASS — Upstream risk protection "
        "overrides intelligence"
    )


# ============================================================
# TEST 12
# CONFIDENCE SCALING DISABLED
# ============================================================

def test_confidence_scaling_disabled():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="INTELLIGENT",
        confidence_scaling_enabled=False,
        intelligent_reference_trades=3,
        max_single_trade_daily_risk_pct=100,
    )

    result = allocator.allocate(
        daily_risk_budget_rupees=30000,
        remaining_daily_risk_rupees=30000,
        trades_taken_today=0,
        setup_score=97,
    )

    print_result(result)

    # Scaling disabled:
    #
    # ₹30,000 / 3 = ₹10,000
    #
    # Score 97 must NOT increase allocation.

    assert_close(
        result["confidence_multiplier"],
        1.00,
        "Disabled confidence scaling "
        "should use 1.00",
    )

    assert_close(
        result["approved_risk_rupees"],
        10000.00,
        "Confidence changed risk despite "
        "scaling being disabled",
    )

    print(
        "✅ PASS — User can disable "
        "confidence-based scaling"
    )


# ============================================================
# TEST 13
# REMAINING BUDGET CANNOT EXCEED DAILY BUDGET
# ============================================================

def test_remaining_budget_sanitization():

    allocator = RiskBudgetAllocator(
        use_dynamic_config=False,
        allocation_mode="INTELLIGENT",
        intelligent_reference_trades=1,
        max_single_trade_daily_risk_pct=100,
        confidence_scaling_enabled=False,
    )

    # Invalid external state claims ₹100,000 remains
    # even though daily risk budget is only ₹50,000.
    #
    # Allocator must sanitize this to ₹50,000.

    result = allocator.allocate(
        daily_risk_budget_rupees=50000,
        remaining_daily_risk_rupees=100000,
        trades_taken_today=0,
        setup_score=90,
    )

    print_result(result)

    assert_close(
        result["remaining_daily_risk_rupees"],
        50000.00,
        "Remaining risk should be capped "
        "at daily budget",
    )

    assert_true(
        result["approved_risk_rupees"]
        <= 50000,
        "Allocator created risk beyond "
        "daily budget",
    )

    print(
        "✅ PASS — Invalid external remaining-risk "
        "value sanitized"
    )


# ============================================================
# RUNNER
# ============================================================

def run_all_tests():

    global PASSED
    global TOTAL

    tests = [
        (
            "TEST 1 — FIXED MODE / THREE TRADE ALLOCATION",
            test_fixed_three_trade_allocation,
        ),
        (
            "TEST 2 — FIXED MODE / ACTUAL REMAINING RISK",
            test_fixed_actual_remaining_budget,
        ),
        (
            "TEST 3 — LOW CONFIDENCE BLOCK",
            test_low_confidence_block,
        ),
        (
            "TEST 4 — INTELLIGENT / MEDIUM SETUP",
            test_intelligent_medium_setup,
        ),
        (
            "TEST 5 — INTELLIGENT / STRONG SETUP",
            test_intelligent_strong_setup,
        ),
        (
            "TEST 6 — INTELLIGENT / 95+ SETUP",
            test_intelligent_very_high_confidence,
        ),
        (
            "TEST 7 — SINGLE TRADE SAFETY CAP",
            test_single_trade_cap,
        ),
        (
            "TEST 8 — REMAINING DAILY RISK PROTECTION",
            test_remaining_daily_budget_protection,
        ),
        (
            "TEST 9 — MAXIMUM TRADE ATTEMPTS",
            test_max_trades_block,
        ),
        (
            "TEST 10 — DAILY RISK EXHAUSTED",
            test_daily_budget_exhausted,
        ),
        (
            "TEST 11 — UPSTREAM HARD BLOCK",
            test_upstream_risk_block,
        ),
        (
            "TEST 12 — CONFIDENCE SCALING DISABLED",
            test_confidence_scaling_disabled,
        ),
        (
            "TEST 13 — DAILY BUDGET SANITIZATION",
            test_remaining_budget_sanitization,
        ),
    ]

    TOTAL = len(
        tests
    )

    heading(
        "🧪 THETA AI TRADER — "
        "RISK BUDGET ALLOCATOR TEST SUITE"
    )

    for title, test in tests:

        heading(
            title
        )

        try:

            test()

            PASSED += 1

        except Exception as error:

            print()
            print(
                "❌ TEST FAILED"
            )

            print(
                "Test :",
                test.__name__,
            )

            print(
                "Error:",
                error,
            )

            print()
            separator()

            print(
                f"❌ RISK BUDGET TESTS FAILED "
                f"({PASSED}/{TOTAL} passed)"
            )

            separator()

            raise

    print()
    separator()

    print(
        f"✅ ALL RISK BUDGET ALLOCATOR TESTS PASSED "
        f"({PASSED}/{TOTAL})"
    )

    print(
        "🔒 TEST ONLY — NO ORDER PLACEMENT"
    )

    separator()


if __name__ == "__main__":
    run_all_tests()