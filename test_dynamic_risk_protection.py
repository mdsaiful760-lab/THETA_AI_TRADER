# ============================================================
# THETA AI TRADER — DYNAMIC RISK PROTECTION TEST
# ============================================================

from config_manager import ConfigManager
from risk_management_engine import RiskManagementEngine


CAPITAL = 1_000_000


def valid_signal():
    return {
        "decision": "TRADE",
        "setup_valid": True,
        "direction": "BULLISH",
        "confidence": "HIGH",
        "trade_permission": "ALLOW",
        "signal_conflict": False,
    }


def normal_volatility():
    return {
        "volatility_state": "NORMAL",
        "spike_detected": False,
        "abnormal_candle": False,
        "rapid_move": False,
    }


def normal_session():
    return {
        "market_open": True,
        "new_entries_allowed": True,
    }


def run_risk(engine, account_state):
    return engine.analyze(
        capital=CAPITAL,
        signal_analysis=valid_signal(),
        account_state=account_state,
        volatility=normal_volatility(),
        session=normal_session(),
        is_expiry_day=False,
    )


def print_result(name, result):
    print()
    print("-" * 76)
    print(name)
    print("-" * 76)

    print(
        "Risk Permission       :",
        result["risk_permission"],
    )

    print(
        "Entry Allowed         :",
        result["entry_allowed"],
    )

    print(
        "Allowed Risk Rs       :",
        result["allowed_risk_rupees"],
    )

    print(
        "Daily Loss %          :",
        round(
            result["daily_loss_pct"],
            2,
        ),
    )

    print(
        "Account Drawdown %    :",
        round(
            result["account_drawdown_pct"],
            2,
        ),
    )

    print(
        "Consecutive Losses    :",
        result["consecutive_losses"],
    )

    print(
        "Open Positions        :",
        result["open_positions"],
    )

    print(
        "Hard Blocks           :",
        result["hard_blocks"],
    )


def assert_blocked(
    result,
    expected_reason,
):
    assert (
        result["risk_permission"]
        == "BLOCK"
    ), (
        "Expected risk permission BLOCK"
    )

    assert (
        result["entry_allowed"]
        is False
    ), (
        "Blocked trade must not allow entry"
    )

    assert (
        result["allowed_risk_rupees"]
        == 0.0
    ), (
        "Blocked trade must have zero allowed risk"
    )

    assert (
        expected_reason
        in result["hard_blocks"]
    ), (
        f"Expected block reason "
        f"{expected_reason}, "
        f"got {result['hard_blocks']}"
    )


def main():

    print("=" * 76)
    print(
        "THETA AI TRADER — "
        "DYNAMIC RISK PROTECTION TEST"
    )
    print("=" * 76)

    config = ConfigManager()

    engine = RiskManagementEngine(
        config_manager=config
    )

    # --------------------------------------------------------
    # STORE ORIGINAL CONFIGURATION
    # --------------------------------------------------------

    original = {
        "max_daily_loss_pct":
            config.get_setting(
                "risk",
                "max_daily_loss_pct",
            ),

        "max_account_drawdown_pct":
            config.get_setting(
                "risk",
                "max_account_drawdown_pct",
            ),

        "max_consecutive_losses":
            config.get_setting(
                "risk",
                "max_consecutive_losses",
            ),

        "max_open_positions":
            config.get_setting(
                "risk",
                "max_open_positions",
            ),
    }

    print()
    print("Original Config Version :", engine.config_version)
    print("Original Daily Limit    :", original["max_daily_loss_pct"])
    print("Original Drawdown Limit :", original["max_account_drawdown_pct"])
    print("Original Loss Limit     :", original["max_consecutive_losses"])
    print("Original Position Limit :", original["max_open_positions"])

    try:

        # ====================================================
        # TEST 1 — DYNAMIC DAILY LOSS LIMIT
        # ====================================================

        config.update_setting(
            "risk",
            "max_daily_loss_pct",
            1.0,
            source="DASHBOARD_TEST",
            user_id="TEST_USER",
        )

        result = run_risk(
            engine,
            {
                "daily_pnl": -10_000,
                "current_equity": CAPITAL,
                "peak_equity": CAPITAL,
                "consecutive_losses": 0,
                "open_positions": 0,
            },
        )

        print_result(
            "TEST 1 — DAILY LOSS LIMIT",
            result,
        )

        assert_blocked(
            result,
            "DAILY_LOSS_LIMIT_REACHED",
        )

        assert (
            engine.max_daily_loss_pct
            == 1.0
        )

        print(
            "✅ PASS — Dynamic daily loss "
            "limit blocked trading"
        )

        # ====================================================
        # TEST 2 — DYNAMIC DRAWDOWN LIMIT
        # ====================================================

        config.update_setting(
            "risk",
            "max_account_drawdown_pct",
            5.0,
            source="DASHBOARD_TEST",
            user_id="TEST_USER",
        )

        result = run_risk(
            engine,
            {
                "daily_pnl": 0,
                "current_equity": 950_000,
                "peak_equity": 1_000_000,
                "consecutive_losses": 0,
                "open_positions": 0,
            },
        )

        print_result(
            "TEST 2 — ACCOUNT DRAWDOWN LIMIT",
            result,
        )

        assert_blocked(
            result,
            "ACCOUNT_DRAWDOWN_LIMIT_REACHED",
        )

        assert (
            engine.max_account_drawdown_pct
            == 5.0
        )

        print(
            "✅ PASS — Dynamic drawdown "
            "limit blocked trading"
        )

        # ====================================================
        # TEST 3 — CONSECUTIVE LOSS LIMIT
        # ====================================================

        config.update_setting(
            "risk",
            "max_consecutive_losses",
            2,
            source="DASHBOARD_TEST",
            user_id="TEST_USER",
        )

        result = run_risk(
            engine,
            {
                "daily_pnl": 0,
                "current_equity": CAPITAL,
                "peak_equity": CAPITAL,
                "consecutive_losses": 2,
                "open_positions": 0,
            },
        )

        print_result(
            "TEST 3 — CONSECUTIVE LOSS LIMIT",
            result,
        )

        assert_blocked(
            result,
            "CONSECUTIVE_LOSS_LIMIT_REACHED",
        )

        assert (
            engine.max_consecutive_losses
            == 2
        )

        print(
            "✅ PASS — Dynamic consecutive "
            "loss limit blocked trading"
        )

        # ====================================================
        # TEST 4 — MAX OPEN POSITIONS
        # ====================================================

        config.update_setting(
            "risk",
            "max_open_positions",
            1,
            source="DASHBOARD_TEST",
            user_id="TEST_USER",
        )

        result = run_risk(
            engine,
            {
                "daily_pnl": 0,
                "current_equity": CAPITAL,
                "peak_equity": CAPITAL,
                "consecutive_losses": 0,
                "open_positions": 1,
            },
        )

        print_result(
            "TEST 4 — MAX OPEN POSITIONS",
            result,
        )

        assert_blocked(
            result,
            "MAX_OPEN_POSITIONS_REACHED",
        )

        assert (
            engine.max_open_positions
            == 1
        )

        print(
            "✅ PASS — Dynamic position "
            "limit blocked trading"
        )

        # ====================================================
        # TEST 5 — MANUAL KILL SWITCH
        # ====================================================

        engine.activate_kill_switch(
            reason="DASHBOARD_EMERGENCY_STOP"
        )

        result = run_risk(
            engine,
            {
                "daily_pnl": 0,
                "current_equity": CAPITAL,
                "peak_equity": CAPITAL,
                "consecutive_losses": 0,
                "open_positions": 0,
            },
        )

        print_result(
            "TEST 5 — MANUAL KILL SWITCH",
            result,
        )

        assert_blocked(
            result,
            "KILL_SWITCH_ACTIVE",
        )

        assert (
            result["kill_switch_active"]
            is True
        )

        assert (
            result["kill_switch_reason"]
            == "DASHBOARD_EMERGENCY_STOP"
        )

        print(
            "✅ PASS — Emergency kill "
            "switch blocked trading"
        )

        # ----------------------------------------------------
        # RESET KILL SWITCH
        # ----------------------------------------------------

        engine.reset_kill_switch()

        # ====================================================
        # TEST 6 — NORMAL CONDITIONS AFTER RESET
        # ====================================================

        result = run_risk(
            engine,
            {
                "daily_pnl": 0,
                "current_equity": CAPITAL,
                "peak_equity": CAPITAL,
                "consecutive_losses": 0,
                "open_positions": 0,
            },
        )

        print_result(
            "TEST 6 — NORMAL CONDITIONS",
            result,
        )

        assert (
            result["risk_permission"]
            == "ALLOW"
        )

        assert (
            result["entry_allowed"]
            is True
        )

        assert (
            result["allowed_risk_rupees"]
            > 0
        )

        assert not result["hard_blocks"]

        print(
            "✅ PASS — Trading restored "
            "after protections cleared"
        )

    finally:

        # ====================================================
        # RESTORE ORIGINAL CONFIGURATION
        # ====================================================

        print()
        print("=" * 76)
        print("RESTORING ORIGINAL RISK CONFIGURATION")
        print("=" * 76)

        engine.reset_kill_switch()

        for key, value in original.items():

            config.update_setting(
                "risk",
                key,
                value,
                source="TEST_RESTORE",
                user_id="TEST_USER",
            )

        engine.refresh_config()

        print(
            "Daily Loss Limit      :",
            engine.max_daily_loss_pct,
        )

        print(
            "Drawdown Limit        :",
            engine.max_account_drawdown_pct,
        )

        print(
            "Consecutive Loss Limit:",
            engine.max_consecutive_losses,
        )

        print(
            "Max Open Positions    :",
            engine.max_open_positions,
        )

        print(
            "Config Version        :",
            engine.config_version,
        )

        assert (
            engine.max_daily_loss_pct
            == float(
                original[
                    "max_daily_loss_pct"
                ]
            )
        )

        assert (
            engine.max_account_drawdown_pct
            == float(
                original[
                    "max_account_drawdown_pct"
                ]
            )
        )

        assert (
            engine.max_consecutive_losses
            == int(
                original[
                    "max_consecutive_losses"
                ]
            )
        )

        assert (
            engine.max_open_positions
            == int(
                original[
                    "max_open_positions"
                ]
            )
        )

        print(
            "✅ Original risk configuration restored"
        )

    print()
    print("=" * 76)
    print(
        "✅ ALL DYNAMIC RISK PROTECTION TESTS PASSED"
    )
    print(
        "🔒 TEST ONLY — NO ORDER PLACEMENT"
    )
    print("=" * 76)


if __name__ == "__main__":
    main()