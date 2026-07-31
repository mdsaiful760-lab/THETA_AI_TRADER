# ============================================================
# THETA AI TRADER
# DYNAMIC POSITION SIZING CONFIGURATION TEST
# ============================================================

from config_manager import ConfigManager
from position_sizing_engine import PositionSizingEngine


def print_separator(character="=", length=76):
    print(character * length)


def run_test():

    print_separator()
    print("THETA AI TRADER — DYNAMIC POSITION SIZING CONFIG TEST")
    print_separator()

    # --------------------------------------------------------
    # INITIALIZE
    # --------------------------------------------------------

    config = ConfigManager()

    # IMPORTANT:
    # We create ONE PositionSizingEngine instance.
    #
    # We will NOT recreate this engine after changing config.
    # This proves that configuration changes are picked up
    # dynamically by the same running engine.
    sizing_engine = PositionSizingEngine(
        config_manager=config
    )

    # --------------------------------------------------------
    # SAVE ORIGINAL CONFIGURATION
    # --------------------------------------------------------

    original_max_lots = config.get_setting(
        "position_sizing",
        "max_lots_per_trade",
    )

    original_expiry_max_lots = config.get_setting(
        "position_sizing",
        "expiry_max_lots_per_trade",
    )

    original_version = config.get_setting(
        "system",
        "config_version",
    )

    print()
    print("Original Config Version :", original_version)
    print("Original Max Lots       :", original_max_lots)
    print(
        "Original Expiry Max Lots:",
        original_expiry_max_lots,
    )

    try:

        # ====================================================
        # TEST 1 — INITIAL ENGINE CONNECTION
        # ====================================================

        print()
        print_separator("-")
        print("TEST 1 — INITIAL ENGINE CONNECTION")
        print_separator("-")

        normal_limits = (
            sizing_engine.get_active_limits(
                is_expiry_day=False
            )
        )

        expiry_limits = (
            sizing_engine.get_active_limits(
                is_expiry_day=True
            )
        )

        print(
            "Normal Active Limit     :",
            normal_limits["active_max_lots"],
        )

        print(
            "Expiry Active Limit     :",
            expiry_limits["active_max_lots"],
        )

        print(
            "Engine Config Version   :",
            sizing_engine.config_version,
        )

        assert (
            normal_limits["max_lots_per_trade"]
            == original_max_lots
        )

        assert (
            expiry_limits[
                "expiry_max_lots_per_trade"
            ]
            == original_expiry_max_lots
        )

        print(
            "✅ PASS — Engine reads initial configuration"
        )

        # ====================================================
        # TEST 2 — CHANGE NORMAL MAX LOTS
        # ====================================================

        print()
        print_separator("-")
        print("TEST 2 — DYNAMIC NORMAL LOT LIMIT")
        print_separator("-")

        # We use 3 because it is safely below the default
        # normal limit and still above the expiry test value.
        new_max_lots = 6

        print(
            "Changing Max Lots / Trade to:",
            new_max_lots,
        )

        update_result = config.update_setting(
            "position_sizing",
            "max_lots_per_trade",
            new_max_lots,
            source="TEST",
        )

        print(
            "Config Changed          :",
            update_result["changed"],
        )

        print(
            "New Config Version      :",
            config.get_setting(
                "system",
                "config_version",
            ),
        )

        # SAME sizing_engine instance.
        normal_limits = (
            sizing_engine.get_active_limits(
                is_expiry_day=False
            )
        )

        print(
            "Engine Max Lots         :",
            normal_limits["max_lots_per_trade"],
        )

        print(
            "Engine Active Limit     :",
            normal_limits["active_max_lots"],
        )

        print(
            "Engine Config Version   :",
            sizing_engine.config_version,
        )

        assert (
            normal_limits["max_lots_per_trade"]
            == new_max_lots
        )

        assert (
            normal_limits["active_max_lots"]
            == new_max_lots
        )

        print(
            "✅ PASS — Same engine detected new normal lot limit"
        )

        # ====================================================
        # TEST 3 — ACTUAL NORMAL POSITION SIZING
        # ====================================================

        print()
        print_separator("-")
        print("TEST 3 — NORMAL POSITION SIZING OBEYS CONFIG")
        print_separator("-")

        risk_analysis = {
            "risk_permission": "ALLOW",
            "entry_allowed": True,

            # Large enough risk budget so CONFIG,
            # not risk, becomes the limiting factor.
            "allowed_risk_rupees": 100000.0,
        }

        normal_result = sizing_engine.analyze(
            risk_analysis=risk_analysis,

            lot_size=75,

            stop_loss_per_unit=10.0,

            margin_per_lot=10000.0,

            available_margin=1000000.0,

            is_expiry_day=False,
        )

        print(
            "Sizing Permission       :",
            normal_result["sizing_permission"],
        )

        print(
            "Position Allowed        :",
            normal_result["position_allowed"],
        )

        print(
            "Lots By Risk            :",
            normal_result["lots_by_risk"],
        )

        print(
            "Lots By Margin          :",
            normal_result["lots_by_margin"],
        )

        print(
            "Lots By Config          :",
            normal_result["lots_by_config"],
        )

        print(
            "Final Lots              :",
            normal_result["final_lots"],
        )

        print(
            "Final Quantity          :",
            normal_result["final_quantity"],
        )

        print(
            "Limiting Factor         :",
            normal_result["limiting_factor"],
        )

        assert (
            normal_result["position_allowed"]
            is True
        )

        assert (
            normal_result["final_lots"]
            == new_max_lots
        )

        assert (
            normal_result["final_quantity"]
            == new_max_lots * 75
        )

        assert (
            normal_result["lots_by_config"]
            == new_max_lots
        )

        print(
            "✅ PASS — Actual sizing obeyed dynamic normal limit"
        )

        # ====================================================
        # TEST 4 — CHANGE EXPIRY LOT LIMIT
        # ====================================================

        print()
        print_separator("-")
        print("TEST 4 — DYNAMIC EXPIRY LOT LIMIT")
        print_separator("-")

        new_expiry_max_lots = 2

        print(
            "Changing Expiry Max Lots to:",
            new_expiry_max_lots,
        )

        update_result = config.update_setting(
            "position_sizing",
            "expiry_max_lots_per_trade",
            new_expiry_max_lots,
            source="TEST",
        )

        print(
            "Config Changed          :",
            update_result["changed"],
        )

        print(
            "New Config Version      :",
            config.get_setting(
                "system",
                "config_version",
            ),
        )

        # Again, SAME engine instance.
        expiry_limits = (
            sizing_engine.get_active_limits(
                is_expiry_day=True
            )
        )

        print(
            "Normal Max Lots         :",
            expiry_limits["max_lots_per_trade"],
        )

        print(
            "Expiry Max Lots         :",
            expiry_limits[
                "expiry_max_lots_per_trade"
            ],
        )

        print(
            "Expiry Active Limit     :",
            expiry_limits["active_max_lots"],
        )

        assert (
            expiry_limits[
                "expiry_max_lots_per_trade"
            ]
            == new_expiry_max_lots
        )

        assert (
            expiry_limits["active_max_lots"]
            == new_expiry_max_lots
        )

        print(
            "✅ PASS — Same engine detected new expiry lot limit"
        )

        # ====================================================
        # TEST 5 — ACTUAL EXPIRY POSITION SIZING
        # ====================================================

        print()
        print_separator("-")
        print("TEST 5 — EXPIRY POSITION SIZING OBEYS CONFIG")
        print_separator("-")

        expiry_result = sizing_engine.analyze(
            risk_analysis=risk_analysis,

            lot_size=75,

            stop_loss_per_unit=10.0,

            margin_per_lot=10000.0,

            available_margin=1000000.0,

            is_expiry_day=True,
        )

        print(
            "Sizing Permission       :",
            expiry_result["sizing_permission"],
        )

        print(
            "Position Allowed        :",
            expiry_result["position_allowed"],
        )

        print(
            "Lots By Risk            :",
            expiry_result["lots_by_risk"],
        )

        print(
            "Lots By Margin          :",
            expiry_result["lots_by_margin"],
        )

        print(
            "Lots By Config          :",
            expiry_result["lots_by_config"],
        )

        print(
            "Final Lots              :",
            expiry_result["final_lots"],
        )

        print(
            "Final Quantity          :",
            expiry_result["final_quantity"],
        )

        print(
            "Limiting Factor         :",
            expiry_result["limiting_factor"],
        )

        assert (
            expiry_result["position_allowed"]
            is True
        )

        assert (
            expiry_result["final_lots"]
            == new_expiry_max_lots
        )

        assert (
            expiry_result["final_quantity"]
            == new_expiry_max_lots * 75
        )

        assert (
            expiry_result["lots_by_config"]
            == new_expiry_max_lots
        )

        print(
            "✅ PASS — Actual expiry sizing obeyed dynamic limit"
        )

        # ====================================================
        # TEST 6 — VERIFY NORMAL LIMIT STILL DIFFERENT
        # ====================================================

        print()
        print_separator("-")
        print("TEST 6 — NORMAL / EXPIRY LIMIT SEPARATION")
        print_separator("-")

        normal_limits = (
            sizing_engine.get_active_limits(
                is_expiry_day=False
            )
        )

        expiry_limits = (
            sizing_engine.get_active_limits(
                is_expiry_day=True
            )
        )

        print(
            "Normal Active Limit     :",
            normal_limits["active_max_lots"],
        )

        print(
            "Expiry Active Limit     :",
            expiry_limits["active_max_lots"],
        )

        assert (
            normal_limits["active_max_lots"]
            == new_max_lots
        )

        assert (
            expiry_limits["active_max_lots"]
            == new_expiry_max_lots
        )

        assert (
            expiry_limits["active_max_lots"]
            <= normal_limits["active_max_lots"]
        )

        print(
            "✅ PASS — Normal and expiry restrictions remain independent"
        )

        print()
        print_separator()
        print(
            "✅ ALL DYNAMIC POSITION SIZING TESTS PASSED"
        )
        print(
            "🔒 TEST ONLY — NO ORDER PLACEMENT"
        )
        print_separator()

    finally:

        # ====================================================
        # ALWAYS RESTORE ORIGINAL CONFIGURATION
        # ====================================================

        print()
        print_separator()
        print("RESTORING ORIGINAL POSITION SIZING CONFIGURATION")
        print_separator()

        # Restore both values together because the
        # relationship validator requires:
        #
        # expiry_max_lots_per_trade <= max_lots_per_trade

        config.update_many(
            {
                "position_sizing": {
                    "max_lots_per_trade": (
                        original_max_lots
                    ),

                    "expiry_max_lots_per_trade": (
                        original_expiry_max_lots
                    ),
                }
            },
            source="TEST_RESTORE",
        )

        restored_normal = config.get_setting(
            "position_sizing",
            "max_lots_per_trade",
        )

        restored_expiry = config.get_setting(
            "position_sizing",
            "expiry_max_lots_per_trade",
        )

        restored_version = config.get_setting(
            "system",
            "config_version",
        )

        print(
            "Restored Max Lots       :",
            restored_normal,
        )

        print(
            "Restored Expiry Max Lots:",
            restored_expiry,
        )

        print(
            "Config Version          :",
            restored_version,
        )

        if (
            restored_normal
            == original_max_lots
            and restored_expiry
            == original_expiry_max_lots
        ):
            print(
                "✅ Original position sizing configuration restored"
            )

        else:
            print(
                "❌ WARNING — Original configuration was not fully restored"
            )

        print_separator()


if __name__ == "__main__":
    run_test()