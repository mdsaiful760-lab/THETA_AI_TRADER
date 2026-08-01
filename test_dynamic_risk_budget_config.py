# ============================================================
# THETA AI TRADER
# FINAL DYNAMIC RISK BUDGET CONFIGURATION TEST
# ============================================================

from config_manager import ConfigManager
from risk_budget_allocator import RiskBudgetAllocator


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


def subheading(title):
    print()
    line("-", 78)
    print(title)
    line("-", 78)


# ============================================================
# ASSERTION HELPERS
# ============================================================

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

    if abs(float(actual) - float(expected)) > tolerance:
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


# ============================================================
# CONFIG HELPERS
# ============================================================

def capture_risk_budget_config(config):

    current = config.get_risk_budget_config()

    return {
        "allocation_mode":
            current["allocation_mode"],

        "max_trades_per_day":
            current["max_trades_per_day"],

        "confidence_scaling_enabled":
            current["confidence_scaling_enabled"],

        "minimum_setup_score":
            current["minimum_setup_score"],

        "max_single_trade_daily_risk_pct":
            current[
                "max_single_trade_daily_risk_pct"
            ],

        "intelligent_reference_trades":
            current[
                "intelligent_reference_trades"
            ],

        "confidence_multiplier_low":
            current[
                "confidence_multiplier_low"
            ],

        "confidence_multiplier_medium":
            current[
                "confidence_multiplier_medium"
            ],

        "confidence_multiplier_high":
            current[
                "confidence_multiplier_high"
            ],

        "confidence_multiplier_exceptional":
            current[
                "confidence_multiplier_exceptional"
            ],
    }


def apply_risk_budget_config(
    config,
    values,
):

    config.update_many(
        {
            "risk_budget": {
                "allocation_mode":
                    values["allocation_mode"],

                "max_trades_per_day":
                    values["max_trades_per_day"],

                "confidence_scaling_enabled":
                    values[
                        "confidence_scaling_enabled"
                    ],

                "minimum_setup_score":
                    values[
                        "minimum_setup_score"
                    ],

                "max_single_trade_daily_risk_pct":
                    values[
                        "max_single_trade_daily_risk_pct"
                    ],

                "intelligent_reference_trades":
                    values[
                        "intelligent_reference_trades"
                    ],

                "confidence_multiplier_low":
                    values[
                        "confidence_multiplier_low"
                    ],

                "confidence_multiplier_medium":
                    values[
                        "confidence_multiplier_medium"
                    ],

                "confidence_multiplier_high":
                    values[
                        "confidence_multiplier_high"
                    ],

                "confidence_multiplier_exceptional":
                    values[
                        "confidence_multiplier_exceptional"
                    ],
            }
        }
    )


def update_risk_budget(
    config,
    key,
    value,
):

    return config.update_setting(
        "risk_budget",
        key,
        value,
    )


def verify_config(
    actual,
    expected,
):

    assert_equal(
        actual["allocation_mode"],
        expected["allocation_mode"],
        "Allocation mode mismatch",
    )

    assert_equal(
        actual["max_trades_per_day"],
        expected["max_trades_per_day"],
        "Maximum trades mismatch",
    )

    assert_equal(
        actual["confidence_scaling_enabled"],
        expected["confidence_scaling_enabled"],
        "Confidence scaling mismatch",
    )

    assert_close(
        actual["minimum_setup_score"],
        expected["minimum_setup_score"],
        "Minimum setup score mismatch",
    )

    assert_close(
        actual[
            "max_single_trade_daily_risk_pct"
        ],
        expected[
            "max_single_trade_daily_risk_pct"
        ],
        "Single-trade cap mismatch",
    )

    assert_equal(
        actual[
            "intelligent_reference_trades"
        ],
        expected[
            "intelligent_reference_trades"
        ],
        "Reference trades mismatch",
    )

    assert_close(
        actual[
            "confidence_multiplier_low"
        ],
        expected[
            "confidence_multiplier_low"
        ],
        "Low multiplier mismatch",
    )

    assert_close(
        actual[
            "confidence_multiplier_medium"
        ],
        expected[
            "confidence_multiplier_medium"
        ],
        "Medium multiplier mismatch",
    )

    assert_close(
        actual[
            "confidence_multiplier_high"
        ],
        expected[
            "confidence_multiplier_high"
        ],
        "High multiplier mismatch",
    )

    assert_close(
        actual[
            "confidence_multiplier_exceptional"
        ],
        expected[
            "confidence_multiplier_exceptional"
        ],
        "Exceptional multiplier mismatch",
    )


# ============================================================
# MAIN TEST
# ============================================================

def run_test():

    config = ConfigManager()

    # ========================================================
    # STEP 1
    # SAVE EXACT USER CONFIGURATION
    # ========================================================

    original_values = capture_risk_budget_config(
        config
    )

    original_version = config.get_setting(
        "system",
        "config_version",
    )

    heading(
        "THETA AI TRADER — FINAL DYNAMIC "
        "RISK BUDGET CONFIG TEST"
    )

    print()
    print(
        "Saved User Config Version :",
        original_version,
    )

    print(
        "Saved Allocation Mode     :",
        original_values[
            "allocation_mode"
        ],
    )

    print(
        "Saved Max Trades          :",
        original_values[
            "max_trades_per_day"
        ],
    )

    print(
        "Saved Confidence Scaling  :",
        original_values[
            "confidence_scaling_enabled"
        ],
    )

    print(
        "Saved Single Trade Cap %  :",
        original_values[
            "max_single_trade_daily_risk_pct"
        ],
    )

    print(
        "Saved Medium Multiplier   :",
        original_values[
            "confidence_multiplier_medium"
        ],
    )

    # ========================================================
    # STEP 2
    # ESTABLISH KNOWN TEST BASELINE
    # ========================================================

    test_baseline = {
        "allocation_mode":
            "FIXED",

        "max_trades_per_day":
            3,

        "confidence_scaling_enabled":
            True,

        "minimum_setup_score":
            60.0,

        "max_single_trade_daily_risk_pct":
            40.0,

        "intelligent_reference_trades":
            3,

        "confidence_multiplier_low":
            0.50,

        "confidence_multiplier_medium":
            0.75,

        "confidence_multiplier_high":
            1.00,

        "confidence_multiplier_exceptional":
            1.25,
    }

    allocator = None

    try:

        subheading(
            "ESTABLISHING ISOLATED TEST BASELINE"
        )

        apply_risk_budget_config(
            config,
            test_baseline,
        )

        baseline = capture_risk_budget_config(
            config
        )

        verify_config(
            baseline,
            test_baseline,
        )

        baseline_version = config.get_setting(
            "system",
            "config_version",
        )

        print(
            "Test Config Version  :",
            baseline_version,
        )

        print(
            "Allocation Mode      :",
            baseline[
                "allocation_mode"
            ],
        )

        print(
            "Max Trades / Day     :",
            baseline[
                "max_trades_per_day"
            ],
        )

        print(
            "Confidence Scaling   :",
            baseline[
                "confidence_scaling_enabled"
            ],
        )

        print(
            "Single Trade Cap %   :",
            baseline[
                "max_single_trade_daily_risk_pct"
            ],
        )

        print(
            "Medium Multiplier    :",
            baseline[
                "confidence_multiplier_medium"
            ],
        )

        print()
        print(
            "✅ Known test baseline established atomically"
        )

        # ====================================================
        # CREATE ONE RUNNING ALLOCATOR
        # ====================================================

        allocator = RiskBudgetAllocator(
            config_manager=config,
            use_dynamic_config=True,
        )

        # ====================================================
        # TEST 1
        # INITIAL CONNECTION
        # ====================================================

        subheading(
            "TEST 1 — INITIAL LIVE CONFIG CONNECTION"
        )

        active = allocator.get_active_config()

        print(
            "Dynamic Config       :",
            active["dynamic_config"],
        )

        print(
            "Allocator Version    :",
            active["config_version"],
        )

        print(
            "Allocation Mode      :",
            active["allocation_mode"],
        )

        print(
            "Max Trades / Day     :",
            active["max_trades_per_day"],
        )

        assert_true(
            active["dynamic_config"],
            "Allocator should use dynamic configuration",
        )

        assert_equal(
            active["allocation_mode"],
            "FIXED",
            "Allocator did not read test baseline mode",
        )

        assert_equal(
            active["max_trades_per_day"],
            3,
            "Allocator did not read test baseline "
            "trade limit",
        )

        assert_close(
            active[
                "confidence_multiplier_medium"
            ],
            0.75,
            "Allocator did not read baseline "
            "medium multiplier",
        )

        print(
            "✅ PASS — Running allocator reads "
            "isolated test configuration"
        )

        # ====================================================
        # TEST 2
        # LIVE FIXED FOUR-TRADE CONFIGURATION
        # ====================================================

        subheading(
            "TEST 2 — LIVE FIXED MODE / "
            "FOUR-TRADE CONFIGURATION"
        )

        config.update_many(
            {
                "risk_budget": {
                    "allocation_mode":
                        "FIXED",

                    "max_trades_per_day":
                        4,

                    "confidence_scaling_enabled":
                        False,

                    "minimum_setup_score":
                        60.0,

                    "max_single_trade_daily_risk_pct":
                        100.0,
                }
            }
        )

        result = allocator.allocate(
            daily_risk_budget_rupees=12000,
            remaining_daily_risk_rupees=12000,
            trades_taken_today=0,
            setup_score=80,
        )

        print(
            "Allocator Version    :",
            result["config_version"],
        )

        print(
            "Allocation Mode      :",
            result["allocation_mode"],
        )

        print(
            "Max Trades / Day     :",
            result["max_trades_per_day"],
        )

        print(
            "Confidence Scaling   :",
            result[
                "confidence_scaling_enabled"
            ],
        )

        print(
            "Approved Risk        :",
            result["approved_risk_rupees"],
        )

        assert_equal(
            result["allocation_mode"],
            "FIXED",
            "Allocator did not remain in FIXED mode",
        )

        assert_equal(
            result["max_trades_per_day"],
            4,
            "Live allocator did not detect "
            "four-trade configuration",
        )

        assert_equal(
            result[
                "confidence_scaling_enabled"
            ],
            False,
            "Live allocator did not disable "
            "confidence scaling",
        )

        assert_close(
            result["approved_risk_rupees"],
            3000.0,
            "₹12,000 / 4 should allocate ₹3,000",
        )

        print(
            "✅ PASS — Same allocator picked up "
            "FIXED / 4-trade changes"
        )

        # ====================================================
        # TEST 3
        # LIVE FIXED -> INTELLIGENT
        # ====================================================

        subheading(
            "TEST 3 — LIVE FIXED → INTELLIGENT SWITCH"
        )

        config.update_many(
            {
                "risk_budget": {
                    "allocation_mode":
                        "INTELLIGENT",

                    "max_trades_per_day":
                        3,

                    "confidence_scaling_enabled":
                        True,

                    "intelligent_reference_trades":
                        3,

                    "max_single_trade_daily_risk_pct":
                        100.0,

                    "confidence_multiplier_low":
                        0.50,

                    "confidence_multiplier_medium":
                        0.75,

                    "confidence_multiplier_high":
                        1.00,

                    "confidence_multiplier_exceptional":
                        1.25,
                }
            }
        )

        result = allocator.allocate(
            daily_risk_budget_rupees=30000,
            remaining_daily_risk_rupees=30000,
            trades_taken_today=0,
            setup_score=80,
        )

        print(
            "Allocator Version    :",
            result["config_version"],
        )

        print(
            "Allocation Mode      :",
            result["allocation_mode"],
        )

        print(
            "Confidence Mult.     :",
            result["confidence_multiplier"],
        )

        print(
            "Base Allocation      :",
            result[
                "base_risk_allocation_rupees"
            ],
        )

        print(
            "Approved Risk        :",
            result["approved_risk_rupees"],
        )

        assert_equal(
            result["allocation_mode"],
            "INTELLIGENT",
            "Allocator did not dynamically "
            "switch to INTELLIGENT",
        )

        assert_close(
            result["confidence_multiplier"],
            0.75,
            "Score 80 should use test medium "
            "multiplier 0.75",
        )

        assert_close(
            result[
                "base_risk_allocation_rupees"
            ],
            10000.0,
            "₹30,000 / 3 reference trades "
            "should equal ₹10,000",
        )

        assert_close(
            result["approved_risk_rupees"],
            7500.0,
            "₹10,000 × 0.75 should equal ₹7,500",
        )

        print(
            "✅ PASS — Running allocator switched "
            "to INTELLIGENT without restart"
        )

        # ====================================================
        # TEST 4
        # LIVE CONFIDENCE MULTIPLIER
        # ====================================================

        subheading(
            "TEST 4 — LIVE CONFIDENCE "
            "MULTIPLIER CHANGE"
        )

        update_risk_budget(
            config,
            "confidence_multiplier_medium",
            0.80,
        )

        result = allocator.allocate(
            daily_risk_budget_rupees=30000,
            remaining_daily_risk_rupees=30000,
            trades_taken_today=0,
            setup_score=80,
        )

        print(
            "Allocator Version    :",
            result["config_version"],
        )

        print(
            "New Medium Mult.     :",
            result["confidence_multiplier"],
        )

        print(
            "Approved Risk        :",
            result["approved_risk_rupees"],
        )

        assert_close(
            result["confidence_multiplier"],
            0.80,
            "Running allocator did not detect "
            "new medium multiplier",
        )

        assert_close(
            result["approved_risk_rupees"],
            8000.0,
            "₹10,000 × 0.80 should equal ₹8,000",
        )

        print(
            "✅ PASS — Confidence multiplier "
            "changed live without restart"
        )

        # ====================================================
        # TEST 5
        # SINGLE TRADE SAFETY CAP
        # ====================================================

        subheading(
            "TEST 5 — LIVE SINGLE-TRADE SAFETY CAP"
        )

        update_risk_budget(
            config,
            "max_single_trade_daily_risk_pct",
            20.0,
        )

        result = allocator.allocate(
            daily_risk_budget_rupees=50000,
            remaining_daily_risk_rupees=50000,
            trades_taken_today=0,
            setup_score=97,
        )

        print(
            "Allocator Version    :",
            result["config_version"],
        )

        print(
            "Setup Score          :",
            result["setup_score"],
        )

        print(
            "Confidence Mult.     :",
            result["confidence_multiplier"],
        )

        print(
            "Single Trade Cap     :",
            result["single_trade_cap_rupees"],
        )

        print(
            "Approved Risk        :",
            result["approved_risk_rupees"],
        )

        assert_close(
            result["confidence_multiplier"],
            1.25,
            "97 score should use exceptional "
            "multiplier",
        )

        assert_close(
            result["single_trade_cap_rupees"],
            10000.0,
            "20% of ₹50,000 should equal ₹10,000",
        )

        assert_close(
            result["approved_risk_rupees"],
            10000.0,
            "Exceptional setup bypassed "
            "single-trade safety cap",
        )

        print(
            "✅ PASS — 95+ setup cannot bypass "
            "single-trade risk cap"
        )

        # ====================================================
        # TEST 6
        # LIVE MAXIMUM TRADE LIMIT
        # ====================================================

        subheading(
            "TEST 6 — LIVE MAXIMUM TRADE LIMIT"
        )

        update_risk_budget(
            config,
            "max_trades_per_day",
            2,
        )

        result = allocator.allocate(
            daily_risk_budget_rupees=50000,
            remaining_daily_risk_rupees=30000,
            trades_taken_today=2,
            setup_score=100,
        )

        print(
            "Allocator Version    :",
            result["config_version"],
        )

        print(
            "Max Trades / Day     :",
            result["max_trades_per_day"],
        )

        print(
            "Trades Taken         :",
            result["trades_taken_today"],
        )

        print(
            "Permission           :",
            result["allocation_permission"],
        )

        print(
            "Reason               :",
            result["reason"],
        )

        assert_equal(
            result["max_trades_per_day"],
            2,
            "Running allocator did not detect "
            "new maximum trade limit",
        )

        assert_equal(
            result["allocation_permission"],
            "BLOCK",
            "Trade should be blocked when "
            "maximum attempts are reached",
        )

        assert_equal(
            result["reason"],
            "MAX_TRADES_PER_DAY_REACHED",
            "Wrong maximum-trades block reason",
        )

        assert_close(
            result["approved_risk_rupees"],
            0.0,
            "Blocked trade must receive zero risk",
        )

        print(
            "✅ PASS — Dashboard maximum-trade "
            "change enforced immediately"
        )

        # ====================================================
        # TEST 7
        # LIVE CONFIDENCE SCALING OFF
        # ====================================================

        subheading(
            "TEST 7 — LIVE CONFIDENCE SCALING OFF"
        )

        config.update_many(
            {
                "risk_budget": {
                    "max_trades_per_day":
                        3,

                    "confidence_scaling_enabled":
                        False,

                    "max_single_trade_daily_risk_pct":
                        100.0,
                }
            }
        )

        result = allocator.allocate(
            daily_risk_budget_rupees=30000,
            remaining_daily_risk_rupees=30000,
            trades_taken_today=0,
            setup_score=97,
        )

        print(
            "Confidence Scaling   :",
            result[
                "confidence_scaling_enabled"
            ],
        )

        print(
            "Confidence Mult.     :",
            result["confidence_multiplier"],
        )

        print(
            "Approved Risk        :",
            result["approved_risk_rupees"],
        )

        assert_equal(
            result[
                "confidence_scaling_enabled"
            ],
            False,
            "Allocator did not dynamically "
            "disable confidence scaling",
        )

        assert_close(
            result["confidence_multiplier"],
            1.0,
            "Scaling OFF must force "
            "multiplier to 1.0",
        )

        assert_close(
            result["approved_risk_rupees"],
            10000.0,
            "97 score must not increase risk "
            "when scaling is OFF",
        )

        print(
            "✅ PASS — Confidence scaling can "
            "be disabled dynamically"
        )

        # ====================================================
        # TEST 8
        # DAILY RISK ABSOLUTE CEILING
        # ====================================================

        subheading(
            "TEST 8 — DAILY RISK REMAINS "
            "ABSOLUTE CEILING"
        )

        config.update_many(
            {
                "risk_budget": {
                    "confidence_scaling_enabled":
                        True,

                    "max_single_trade_daily_risk_pct":
                        100.0,
                }
            }
        )

        result = allocator.allocate(
            daily_risk_budget_rupees=50000,
            remaining_daily_risk_rupees=6000,
            trades_taken_today=2,
            setup_score=100,
        )

        print(
            "Remaining Daily Risk:",
            result[
                "remaining_daily_risk_rupees"
            ],
        )

        print(
            "Confidence Mult.     :",
            result["confidence_multiplier"],
        )

        print(
            "Approved Risk        :",
            result["approved_risk_rupees"],
        )

        assert_close(
            result["confidence_multiplier"],
            1.25,
            "100 score should use exceptional "
            "multiplier",
        )

        assert_true(
            result["approved_risk_rupees"]
            <= result[
                "remaining_daily_risk_rupees"
            ],
            "Approved risk exceeded remaining "
            "daily risk",
        )

        assert_close(
            result["approved_risk_rupees"],
            6000.0,
            "Remaining ₹6,000 must remain "
            "absolute risk ceiling",
        )

        print(
            "✅ PASS — Dynamic intelligence cannot "
            "create additional daily risk"
        )

        # ====================================================
        # SUCCESS
        # ====================================================

        heading(
            "✅ ALL DYNAMIC RISK BUDGET "
            "CONFIG TESTS PASSED (8/8)"
        )

        print(
            "🔒 SAME ALLOCATOR INSTANCE USED THROUGHOUT"
        )

        print(
            "🔒 TEST CONFIGURATION ISOLATED "
            "FROM USER CONFIGURATION"
        )

        print(
            "🔒 TEST ONLY — NO ORDER PLACEMENT"
        )

    finally:

        # ====================================================
        # ALWAYS RESTORE EXACT USER CONFIGURATION
        # ====================================================

        heading(
            "RESTORING SAVED USER "
            "RISK BUDGET CONFIGURATION"
        )

        try:

            apply_risk_budget_config(
                config,
                original_values,
            )

            restored_config = (
                capture_risk_budget_config(
                    config
                )
            )

            verify_config(
                restored_config,
                original_values,
            )

            if allocator is not None:
                allocator.refresh_config()

            final_version = config.get_setting(
                "system",
                "config_version",
            )

            print()
            print(
                "Restored Allocation Mode:",
                restored_config[
                    "allocation_mode"
                ],
            )

            print(
                "Restored Max Trades     :",
                restored_config[
                    "max_trades_per_day"
                ],
            )

            print(
                "Restored Scaling        :",
                restored_config[
                    "confidence_scaling_enabled"
                ],
            )

            print(
                "Restored Min Score      :",
                restored_config[
                    "minimum_setup_score"
                ],
            )

            print(
                "Restored Single Cap %   :",
                restored_config[
                    "max_single_trade_daily_risk_pct"
                ],
            )

            print(
                "Restored Reference      :",
                restored_config[
                    "intelligent_reference_trades"
                ],
            )

            print(
                "Restored Low Mult.      :",
                restored_config[
                    "confidence_multiplier_low"
                ],
            )

            print(
                "Restored Medium Mult.   :",
                restored_config[
                    "confidence_multiplier_medium"
                ],
            )

            print(
                "Restored High Mult.     :",
                restored_config[
                    "confidence_multiplier_high"
                ],
            )

            print(
                "Restored Exceptional    :",
                restored_config[
                    "confidence_multiplier_exceptional"
                ],
            )

            print(
                "Final Config Version    :",
                final_version,
            )

            print()
            print(
                "✅ Exact saved user configuration "
                "restored atomically"
            )

            print(
                "✅ Restoration verification passed"
            )

        except Exception as restore_error:

            print()
            print(
                "❌ CRITICAL — USER CONFIG "
                "RESTORATION FAILED"
            )

            print(
                "Error:",
                restore_error,
            )

            raise


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    run_test()