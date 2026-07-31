# ============================================================
# THETA AI TRADER — DYNAMIC RISK CONFIG TEST
# ============================================================

from config_manager import ConfigManager
from risk_management_engine import RiskManagementEngine


def build_valid_signal():
    return {
        "decision": "TRADE",
        "setup_valid": True,
        "direction": "BULLISH",
        "confidence": "HIGH",
        "trade_permission": "ALLOW",
        "signal_conflict": False,
    }


def build_account_state(capital):
    return {
        "daily_pnl": 0.0,
        "current_equity": capital,
        "peak_equity": capital,
        "consecutive_losses": 0,
        "open_positions": 0,
    }


def build_volatility():
    return {
        "volatility_state": "NORMAL",
        "spike_detected": False,
        "abnormal_candle": False,
        "rapid_move": False,
    }


def build_session():
    return {
        "market_open": True,
        "new_entries_allowed": True,
    }


def main():

    print("=" * 72)
    print("THETA AI TRADER — DYNAMIC RISK CONFIG TEST")
    print("=" * 72)

    config = ConfigManager()

    risk_engine = RiskManagementEngine(
        config_manager=config
    )

    capital = 1_000_000

    original_risk = config.get_setting(
        "risk",
        "max_risk_per_trade_pct",
    )

    print()
    print("Original Config Version :", risk_engine.config_version)
    print("Original Risk / Trade   :", original_risk)

    try:

        # ----------------------------------------------------
        # SIMULATE DASHBOARD CONFIGURATION CHANGE
        # ----------------------------------------------------

        print()
        print("Changing Max Risk/Trade to 0.50%...")

        config.update_setting(
            "risk",
            "max_risk_per_trade_pct",
            0.50,
            source="DASHBOARD_TEST",
            user_id="TEST_USER",
        )

        # IMPORTANT:
        # We intentionally DO NOT call:
        #
        #     risk_engine.refresh_config()
        #
        # analyze() must automatically reload the latest
        # configuration.

        result = risk_engine.analyze(
            capital=capital,
            signal_analysis=build_valid_signal(),
            account_state=build_account_state(
                capital
            ),
            volatility=build_volatility(),
            session=build_session(),
            is_expiry_day=False,
        )

        print()
        print("-" * 72)
        print("RISK ENGINE RESULT")
        print("-" * 72)

        print(
            "Config Version        :",
            result["config_version"],
        )

        print(
            "Dynamic Config        :",
            result["dynamic_config_enabled"],
        )

        print(
            "Max Risk / Trade      :",
            result["max_risk_per_trade_pct"],
        )

        print(
            "Capital               :",
            result["capital"],
        )

        print(
            "Allowed Risk %        :",
            result["allowed_risk_pct"],
        )

        print(
            "Allowed Risk Rs       :",
            result["allowed_risk_rupees"],
        )

        print(
            "Risk Permission       :",
            result["risk_permission"],
        )

        print(
            "Entry Allowed         :",
            result["entry_allowed"],
        )

        print(
            "Hard Blocks           :",
            result["hard_blocks"],
        )

        # ----------------------------------------------------
        # ASSERTIONS
        # ----------------------------------------------------

        assert (
            result["dynamic_config_enabled"]
            is True
        ), (
            "Dynamic configuration is not enabled"
        )

        assert (
            result["max_risk_per_trade_pct"]
            == 0.50
        ), (
            "RiskManagementEngine did not reload "
            "the changed risk setting"
        )

        assert (
            result["allowed_risk_pct"]
            == 0.50
        ), (
            "Allowed risk percentage is incorrect"
        )

        assert (
            result["allowed_risk_rupees"]
            == 5000.0
        ), (
            "Allowed rupee risk should be Rs 5,000 "
            "for Rs 10,00,000 capital at 0.50%"
        )

        assert (
            result["risk_permission"]
            == "ALLOW"
        ), (
            "Normal valid setup should be allowed"
        )

        assert (
            result["entry_allowed"]
            is True
        ), (
            "Valid setup should allow entry"
        )

        assert not result["hard_blocks"], (
            "Unexpected hard risk block detected"
        )

        print()
        print(
            "✅ PASS — ConfigManager change automatically "
            "reached RiskManagementEngine"
        )

    finally:

        # ----------------------------------------------------
        # ALWAYS RESTORE ORIGINAL CONFIGURATION
        # ----------------------------------------------------

        print()
        print("-" * 72)
        print("RESTORING ORIGINAL CONFIGURATION")
        print("-" * 72)

        config.update_setting(
            "risk",
            "max_risk_per_trade_pct",
            original_risk,
            source="TEST_RESTORE",
            user_id="TEST_USER",
        )

        risk_engine.refresh_config()

        restored_risk = (
            risk_engine.max_risk_per_trade_pct
        )

        print(
            "Restored Risk / Trade :",
            restored_risk,
        )

        print(
            "Config Version        :",
            risk_engine.config_version,
        )

        assert (
            restored_risk
            == float(original_risk)
        ), (
            "Original risk configuration "
            "was not restored"
        )

        print(
            "✅ Original configuration restored"
        )

    print()
    print("=" * 72)
    print("✅ DYNAMIC RISK CONFIG TEST PASSED")
    print("🔒 TEST ONLY — NO ORDER PLACEMENT")
    print("=" * 72)


if __name__ == "__main__":
    main()