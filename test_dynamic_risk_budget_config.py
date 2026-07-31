# ============================================================
# THETA AI TRADER
# DYNAMIC RISK BUDGET CONFIGURATION TEST
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
        float(actual) - float(expected)
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


# ============================================================
# CONFIG UPDATE HELPER
# ============================================================

def update_risk_budget(
    config,
    key,
    value,
):

    result = config.update_setting(
        "risk_budget",
        key,
        value,
    )

    return result


# ============================================================
# MAIN TEST
# ============================================================

def run_test():

    config = ConfigManager()

    # --------------------------------------------------------
    # SAVE ORIGINAL CONFIGURATION
    # --------------------------------------------------------

    original = config.get_risk_budget_config()

    original_values = {
        "allocation_mode":
            original["allocation_mode"],

        "max_trades_per_day":
            original["max_trades_per_day"],

        "confidence_scaling_enabled":
            original["confidence_scaling_enabled"],

        "minimum_setup_score":
            original["minimum_setup_score"],

        "max_single_trade_daily_risk_pct":
            original[
                "max_single_trade_daily_risk_pct"
            ],

        "intelligent_reference_trades":
            original[
                "intelligent_reference_trades"
            ],

        "confidence_multiplier_low":
            original[
                "confidence_multiplier_low"
            ],

        "confidence_multiplier_medium":
            original[
                "confidence_multiplier_medium"
            ],

        "confidence_multiplier_high":
            original[
                "confidence_multiplier_high"
            ],

        "confidence_multiplier_exceptional":
            original[
                "confidence_multiplier_exceptional"
            ],
    }

    original_version = config.get_setting(
        "system",
        "config_version",
    )

    # --------------------------------------------------------
    # CREATE ONE ALLOCATOR INSTANCE
    # --------------------------------------------------------
    #
    # IMPORTANT:
    #
    # We intentionally NEVER recreate this allocator during
    # the tests.
    #
    # This proves that configuration changes reach an
    # already-running production object.

    allocator = RiskBudgetAllocator(
        config_manager=config,
        use_dynamic_config=True,
    )

    heading(
        "THETA AI TRADER — "
        "DYNAMIC RISK BUDGET CONFIG TEST"
    )

    print()
    print(
        "Original Config Version :",
        original_version,
    )

    print(
        "Original Allocation Mode:",
        original_values[
            "allocation_mode"
        ],
    )

    print(
        "Original Max Trades     :",
        original_values[
            "max_trades_per_day"
        ],
    )

    print(
        "Original Scaling        :",
        original_values[
            "confidence_scaling_enabled"
        ],
    )

    print(
        "Original Single Cap %   :",
        original_values[
            "max_single_trade_daily_risk_pct"
        ],
    )

    try:

        # ====================================================
        # TEST 1
        # INITIAL LIVE CONNECTION
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
            "Allocator should be dynamically configured",
        )

        assert_equal(
            active["allocation_mode"],
            original_values[
                "allocation_mode"
            ],
            "Allocator did not read initial allocation mode",
        )

        assert_equal(
            active["max_trades_per_day"],
            original_values[
                "max_trades_per_day"
            ],
            "Allocator did not read initial trade limit",
        )

        print(
            "✅ PASS — Running allocator reads "
            "ConfigManager"
        )

        # ====================================================
        # TEST 2
        # FIXED MODE + FOUR TRADES
        # ====================================================

        subheading(
            "TEST 2 — LIVE FIXED MODE / "
            "FOUR-TRADE CONFIGURATION"
        )

        update_risk_budget(
            config,
            "allocation_mode",
            "FIXED",
        )

        update_risk_budget(
            config,
            "max_trades_per_day",
            4,
        )

        update_risk_budget(
            config,
            "confidence_scaling_enabled",
            False,
        )

        update_risk_budget(
            config,
            "minimum_setup_score",
            60.0,
        )

        update_risk_budget(
            config,
            "max_single_trade_daily_risk_pct",
            100.0,
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
            "Live allocator did not switch to FIXED",
        )

        assert_equal(
            result["max_trades_per_day"],
            4,
            "Live allocator did not pick up "
            "four-trade configuration",
        )

        assert_equal(
            result[
                "confidence_scaling_enabled"
            ],
            False,
            "Live allocator did not disable scaling",
        )

        # ₹12,000 / 4 = ₹3,000

        assert_close(
            result["approved_risk_rupees"],
            3000.0,
            "FIXED four-trade allocation incorrect",
        )

        print(
            "✅ PASS — Same allocator picked up "
            "FIXED / 4-trade dashboard changes"
        )

        # ====================================================
        # TEST 3
        # CHANGE TO INTELLIGENT MODE
        # ====================================================

        subheading(
            "TEST 3 — LIVE FIXED → INTELLIGENT SWITCH"
        )

        update_risk_budget(
            config,
            "allocation_mode",
            "INTELLIGENT",
        )

        update_risk_budget(
            config,
            "max_trades_per_day",
            3,
        )

        update_risk_budget(
            config,
            "confidence_scaling_enabled",
            True,
        )

        update_risk_budget(
            config,
            "intelligent_reference_trades",
            3,
        )

        update_risk_budget(
            config,
            "max_single_trade_daily_risk_pct",
            100.0,
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
            "Same allocator did not switch "
            "to INTELLIGENT",
        )

        assert_close(
            result["confidence_multiplier"],
            0.75,
            "Score 80 should currently use "
            "medium multiplier",
        )

        # Base:
        # ₹30,000 / 3 = ₹10,000
        #
        # Medium:
        # ₹10,000 × 0.75 = ₹7,500

        assert_close(
            result["approved_risk_rupees"],
            7500.0,
            "INTELLIGENT allocation incorrect",
        )

        print(
            "✅ PASS — Running allocator switched "
            "to INTELLIGENT without restart"
        )

        # ====================================================
        # TEST 4
        # LIVE CONFIDENCE MULTIPLIER CHANGE
        # ====================================================

        subheading(
            "TEST 4 — LIVE CONFIDENCE "
            "MULTIPLIER CHANGE"
        )

        # Change medium multiplier:
        #
        # 0.75 -> 0.80
        #
        # We must keep multiplier ordering valid:
        #
        # low <= medium <= high <= exceptional

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

        # ₹10,000 × 0.80 = ₹8,000

        assert_close(
            result["confidence_multiplier"],
            0.80,
            "Allocator did not pick up new "
            "medium confidence multiplier",
        )

        assert_close(
            result["approved_risk_rupees"],
            8000.0,
            "Updated confidence multiplier "
            "did not affect allocation",
        )

        print(
            "✅ PASS — Confidence multiplier changed "
            "live without restart"
        )

        # ====================================================
        # TEST 5
        # SINGLE-TRADE CAP OVERRIDES 95+ CONFIDENCE
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

        # 20% of ₹50,000 = ₹10,000.
        #
        # Exceptional confidence may want more,
        # but hard cap must win.

        assert_close(
            result["single_trade_cap_rupees"],
            10000.0,
            "Single-trade cap calculation incorrect",
        )

        assert_close(
            result["approved_risk_rupees"],
            10000.0,
            "Exceptional setup bypassed "
            "single-trade risk cap",
        )

        print(
            "✅ PASS — 95+ setup cannot bypass "
            "dashboard risk cap"
        )

        # ====================================================
        # TEST 6
        # LIVE MAXIMUM TRADES CHANGE
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
            "Allocator did not pick up "
            "new max-trades setting",
        )

        assert_equal(
            result["allocation_permission"],
            "BLOCK",
            "Trade should be blocked after "
            "new maximum is reached",
        )

        assert_equal(
            result["reason"],
            "MAX_TRADES_PER_DAY_REACHED",
            "Wrong maximum-trade block reason",
        )

        print(
            "✅ PASS — Dashboard maximum-trade "
            "change enforced immediately"
        )

        # ====================================================
        # TEST 7
        # DISABLE CONFIDENCE SCALING LIVE
        # ====================================================

        subheading(
            "TEST 7 — LIVE CONFIDENCE SCALING OFF"
        )

        update_risk_budget(
            config,
            "max_trades_per_day",
            3,
        )

        update_risk_budget(
            config,
            "confidence_scaling_enabled",
            False,
        )

        update_risk_budget(
            config,
            "max_single_trade_daily_risk_pct",
            100.0,
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
            "Allocator did not disable "
            "confidence scaling",
        )

        assert_close(
            result["confidence_multiplier"],
            1.0,
            "Scaling OFF should force "
            "multiplier to 1.0",
        )

        # ₹30,000 / 3 = ₹10,000
        #
        # Score 97 must NOT increase risk.

        assert_close(
            result["approved_risk_rupees"],
            10000.0,
            "Exceptional score changed risk "
            "while scaling was disabled",
        )

        print(
            "✅ PASS — User can disable confidence "
            "scaling from configuration"
        )

        # ====================================================
        # TEST 8
        # DAILY RISK REMAINS ABSOLUTE CEILING
        # ====================================================

        subheading(
            "TEST 8 — DAILY RISK REMAINS "
            "ABSOLUTE CEILING"
        )

        update_risk_budget(
            config,
            "confidence_scaling_enabled",
            True,
        )

        update_risk_budget(
            config,
            "max_single_trade_daily_risk_pct",
            100.0,
        )

        result = allocator.allocate(
            daily_risk_budget_rupees=50000,

            # Only ₹6,000 remains.
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

        assert_true(
            result["approved_risk_rupees"]
            <= 6000.0,
            "Dynamic configuration allowed risk "
            "beyond remaining daily budget",
        )

        assert_close(
            result["approved_risk_rupees"],
            6000.0,
            "Remaining daily risk should be "
            "the absolute ceiling",
        )

        print(
            "✅ PASS — Dynamic intelligence cannot "
            "create extra daily risk"
        )

        heading(
            "✅ ALL DYNAMIC RISK BUDGET "
            "CONFIG TESTS PASSED (8/8)"
        )

        print(
            "🔒 SAME ALLOCATOR INSTANCE USED "
            "THROUGHOUT"
        )

        print(
            "🔒 TEST ONLY — NO ORDER PLACEMENT"
        )

    finally:

        # ====================================================
        # RESTORE ORIGINAL CONFIGURATION ATOMICALLY
        # ====================================================
        #
        # IMPORTANT:
        #
        # Risk-budget settings have relationships such as:
        #
        # low <= medium <= high <= exceptional
        #
        # Therefore related values must be restored together
        # as ONE atomic configuration transaction.
        #
        # This prevents valid final settings from being
        # rejected because of an invalid temporary state.

        heading(
            "RESTORING ORIGINAL "
            "RISK BUDGET CONFIGURATION"
        )

        try:

            updates = {
                "risk_budget": {
                    "allocation_mode":
                        original_values[
                            "allocation_mode"
                        ],

                    "max_trades_per_day":
                        original_values[
                            "max_trades_per_day"
                        ],

                    "confidence_scaling_enabled":
                        original_values[
                            "confidence_scaling_enabled"
                        ],

                    "minimum_setup_score":
                        original_values[
                            "minimum_setup_score"
                        ],

                    "max_single_trade_daily_risk_pct":
                        original_values[
                            "max_single_trade_daily_risk_pct"
                        ],

                    "intelligent_reference_trades":
                        original_values[
                            "intelligent_reference_trades"
                        ],

                    "confidence_multiplier_low":
                        original_values[
                            "confidence_multiplier_low"
                        ],

                    "confidence_multiplier_medium":
                        original_values[
                            "confidence_multiplier_medium"
                        ],

                    "confidence_multiplier_high":
                        original_values[
                            "confidence_multiplier_high"
                        ],

                    "confidence_multiplier_exceptional":
                        original_values[
                            "confidence_multiplier_exceptional"
                        ],
                }
            }

            # ------------------------------------------------
            # ATOMIC RESTORE
            # ------------------------------------------------

            config.update_many(
                updates
            )

            # ------------------------------------------------
            # REFRESH SAME RUNNING ALLOCATOR
            # ------------------------------------------------

            allocator.refresh_config()

            restored = allocator.get_active_config(
                refresh=False
            )

            print()
            print(
                "Restored Allocation Mode:",
                restored[
                    "allocation_mode"
                ],
            )

            print(
                "Restored Max Trades     :",
                restored[
                    "max_trades_per_day"
                ],
            )

            print(
                "Restored Scaling        :",
                restored[
                    "confidence_scaling_enabled"
                ],
            )

            print(
                "Restored Min Score      :",
                restored[
                    "minimum_setup_score"
                ],
            )

            print(
                "Restored Single Cap %   :",
                restored[
                    "max_single_trade_daily_risk_pct"
                ],
            )

            print(
                "Restored Reference      :",
                restored[
                    "intelligent_reference_trades"
                ],
            )

            print(
                "Restored Low Mult.      :",
                restored[
                    "confidence_multiplier_low"
                ],
            )

            print(
                "Restored Medium Mult.   :",
                restored[
                    "confidence_multiplier_medium"
                ],
            )

            print(
                "Restored High Mult.     :",
                restored[
                    "confidence_multiplier_high"
                ],
            )

            print(
                "Restored Exceptional    :",
                restored[
                    "confidence_multiplier_exceptional"
                ],
            )

            print(
                "Final Config Version    :",
                restored[
                    "config_version"
                ],
            )

            # =================================================
            # VERIFY RESTORATION
            # =================================================

            assert_equal(
                restored["allocation_mode"],
                original_values[
                    "allocation_mode"
                ],
                "Allocation mode was not restored",
            )

            assert_equal(
                restored["max_trades_per_day"],
                original_values[
                    "max_trades_per_day"
                ],
                "Max trades was not restored",
            )

            assert_equal(
                restored[
                    "confidence_scaling_enabled"
                ],
                original_values[
                    "confidence_scaling_enabled"
                ],
                "Confidence scaling was not restored",
            )

            assert_close(
                restored[
                    "minimum_setup_score"
                ],
                original_values[
                    "minimum_setup_score"
                ],
                "Minimum setup score was not restored",
            )

            assert_close(
                restored[
                    "max_single_trade_daily_risk_pct"
                ],
                original_values[
                    "max_single_trade_daily_risk_pct"
                ],
                "Single-trade cap was not restored",
            )

            assert_equal(
                restored[
                    "intelligent_reference_trades"
                ],
                original_values[
                    "intelligent_reference_trades"
                ],
                "Reference trades were not restored",
            )

            assert_close(
                restored[
                    "confidence_multiplier_low"
                ],
                original_values[
                    "confidence_multiplier_low"
                ],
                "Low multiplier was not restored",
            )

            assert_close(
                restored[
                    "confidence_multiplier_medium"
                ],
                original_values[
                    "confidence_multiplier_medium"
                ],
                "Medium multiplier was not restored",
            )

            assert_close(
                restored[
                    "confidence_multiplier_high"
                ],
                original_values[
                    "confidence_multiplier_high"
                ],
                "High multiplier was not restored",
            )

            assert_close(
                restored[
                    "confidence_multiplier_exceptional"
                ],
                original_values[
                    "confidence_multiplier_exceptional"
                ],
                "Exceptional multiplier was not restored",
            )

            print()
            print(
                "✅ Original risk budget "
                "configuration restored atomically"
            )

            print(
                "✅ Restoration verification passed"
            )

        except Exception as restore_error:

            print()
            print(
                "❌ CRITICAL — CONFIG RESTORATION FAILED"
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