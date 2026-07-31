from config_manager import ConfigManager


def main():

    config = ConfigManager()

    print("=" * 76)
    print("THETA AI TRADER — RESTORE RISK BUDGET DEFAULTS")
    print("=" * 76)

    print()
    print("Current Config Version:",
          config.get_setting(
              "system",
              "config_version",
          ))

    # ========================================================
    # RESTORE THE INTENDED BASELINE
    # ========================================================
    #
    # All related settings are changed together so
    # ConfigManager validates the final configuration
    # atomically.
    # ========================================================

    updates = {
        "risk_budget": {
            "allocation_mode": "FIXED",
            "max_trades_per_day": 3,
            "confidence_scaling_enabled": True,
            "minimum_setup_score": 60.0,
            "max_single_trade_daily_risk_pct": 40.0,
            "intelligent_reference_trades": 3,

            "confidence_multiplier_low": 0.50,
            "confidence_multiplier_medium": 0.75,
            "confidence_multiplier_high": 1.00,
            "confidence_multiplier_exceptional": 1.25,
        }
    }

    config.update_many(
        updates
    )

    # ========================================================
    # READ BACK FROM CONFIG MANAGER
    # ========================================================

    restored = config.get_risk_budget_config()

    version = config.get_setting(
        "system",
        "config_version",
    )

    print()
    print("-" * 76)
    print("RESTORED CONFIGURATION")
    print("-" * 76)

    print(
        "Allocation Mode      :",
        restored["allocation_mode"],
    )

    print(
        "Max Trades / Day     :",
        restored["max_trades_per_day"],
    )

    print(
        "Confidence Scaling   :",
        restored["confidence_scaling_enabled"],
    )

    print(
        "Minimum Setup Score  :",
        restored["minimum_setup_score"],
    )

    print(
        "Single Trade Cap %   :",
        restored[
            "max_single_trade_daily_risk_pct"
        ],
    )

    print(
        "Reference Trades     :",
        restored[
            "intelligent_reference_trades"
        ],
    )

    print(
        "Low Multiplier       :",
        restored[
            "confidence_multiplier_low"
        ],
    )

    print(
        "Medium Multiplier    :",
        restored[
            "confidence_multiplier_medium"
        ],
    )

    print(
        "High Multiplier      :",
        restored[
            "confidence_multiplier_high"
        ],
    )

    print(
        "Exceptional Mult.    :",
        restored[
            "confidence_multiplier_exceptional"
        ],
    )

    print(
        "Config Version       :",
        version,
    )

    # ========================================================
    # VERIFY
    # ========================================================

    expected = {
        "allocation_mode": "FIXED",
        "max_trades_per_day": 3,
        "confidence_scaling_enabled": True,
        "minimum_setup_score": 60.0,
        "max_single_trade_daily_risk_pct": 40.0,
        "intelligent_reference_trades": 3,
        "confidence_multiplier_low": 0.50,
        "confidence_multiplier_medium": 0.75,
        "confidence_multiplier_high": 1.00,
        "confidence_multiplier_exceptional": 1.25,
    }

    for key, expected_value in expected.items():

        actual_value = restored[key]

        if actual_value != expected_value:

            raise AssertionError(
                f"{key} restoration failed. "
                f"Expected={expected_value}, "
                f"Actual={actual_value}"
            )

    print()
    print("=" * 76)
    print("✅ RISK BUDGET BASELINE RESTORED")
    print("✅ RESTORATION VERIFIED")
    print("=" * 76)


if __name__ == "__main__":
    main()
