# ============================================================
# THETA AI TRADER — CONFIG MANAGER TEST SUITE
# ============================================================

import json
import os
import tempfile

from config_manager import ConfigManager


# ============================================================
# TEST HELPERS
# ============================================================

def print_header(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}"
        )


def assert_true(value, message):
    if not value:
        raise AssertionError(message)


def create_test_manager(temp_dir):
    return ConfigManager(
        config_file=os.path.join(
            temp_dir,
            "user_config.json",
        ),
        history_file=os.path.join(
            temp_dir,
            "config_history.jsonl",
        ),
    )


# ============================================================
# TEST 1 — DEFAULT CONFIGURATION
# ============================================================

def test_default_configuration():

    print_header(
        "TEST 1 — DEFAULT CONFIGURATION"
    )

    with tempfile.TemporaryDirectory() as temp_dir:

        manager = create_test_manager(
            temp_dir
        )

        assert_equal(
            manager.get_setting(
                "risk",
                "max_risk_per_trade_pct",
            ),
            1.0,
            "Incorrect default risk per trade",
        )

        assert_equal(
            manager.get_setting(
                "risk",
                "max_daily_loss_pct",
            ),
            3.0,
            "Incorrect default daily loss limit",
        )

        assert_equal(
            manager.get_setting(
                "risk",
                "max_account_drawdown_pct",
            ),
            10.0,
            "Incorrect default drawdown limit",
        )

        assert_equal(
            manager.get_setting(
                "system",
                "environment",
            ),
            "PAPER",
            "Default environment must be PAPER",
        )

        print("Max Risk/Trade :", 1.0)
        print("Daily Loss     :", 3.0)
        print("Drawdown       :", 10.0)
        print("Environment    :", "PAPER")

        print(
            "✅ PASS — Default configuration"
        )


# ============================================================
# TEST 2 — DASHBOARD STYLE SINGLE UPDATE
# ============================================================

def test_dashboard_single_update():

    print_header(
        "TEST 2 — DASHBOARD STYLE RISK UPDATE"
    )

    with tempfile.TemporaryDirectory() as temp_dir:

        manager = create_test_manager(
            temp_dir
        )

        old_version = manager.get_setting(
            "system",
            "config_version",
        )

        result = manager.update_setting(
            "risk",
            "max_risk_per_trade_pct",
            0.50,
            source="DASHBOARD",
            user_id="TEST_USER",
        )

        assert_true(
            result["changed"],
            "Risk setting should have changed",
        )

        assert_equal(
            manager.get_setting(
                "risk",
                "max_risk_per_trade_pct",
            ),
            0.50,
            "Dashboard risk update failed",
        )

        new_version = manager.get_setting(
            "system",
            "config_version",
        )

        assert_equal(
            new_version,
            old_version + 1,
            "Config version did not increment",
        )

        print(
            "Old Risk/Trade :",
            result["old_value"],
        )

        print(
            "New Risk/Trade :",
            result["new_value"],
        )

        print(
            "Old Version    :",
            old_version,
        )

        print(
            "New Version    :",
            new_version,
        )

        print(
            "✅ PASS — Dashboard update persisted"
        )


# ============================================================
# TEST 3 — CONFIGURATION SURVIVES RESTART
# ============================================================

def test_configuration_persistence():

    print_header(
        "TEST 3 — CONFIGURATION PERSISTENCE"
    )

    with tempfile.TemporaryDirectory() as temp_dir:

        manager_1 = create_test_manager(
            temp_dir
        )

        manager_1.update_setting(
            "risk",
            "max_risk_per_trade_pct",
            0.75,
            source="DASHBOARD",
        )

        manager_1.update_setting(
            "risk",
            "max_daily_loss_pct",
            2.0,
            source="DASHBOARD",
        )

        # Simulate application restart.
        manager_2 = create_test_manager(
            temp_dir
        )

        risk_per_trade = (
            manager_2.get_setting(
                "risk",
                "max_risk_per_trade_pct",
            )
        )

        daily_loss = (
            manager_2.get_setting(
                "risk",
                "max_daily_loss_pct",
            )
        )

        assert_equal(
            risk_per_trade,
            0.75,
            "Risk setting did not survive restart",
        )

        assert_equal(
            daily_loss,
            2.0,
            "Daily loss setting did not survive restart",
        )

        print(
            "Reloaded Risk/Trade :",
            risk_per_trade,
        )

        print(
            "Reloaded Daily Loss :",
            daily_loss,
        )

        print(
            "✅ PASS — Configuration survives restart"
        )


# ============================================================
# TEST 4 — MULTIPLE DASHBOARD SETTINGS
# ============================================================

def test_multiple_settings():

    print_header(
        "TEST 4 — ATOMIC MULTIPLE SETTINGS UPDATE"
    )

    with tempfile.TemporaryDirectory() as temp_dir:

        manager = create_test_manager(
            temp_dir
        )

        old_version = manager.get_setting(
            "system",
            "config_version",
        )

        result = manager.update_many(
            {
                "risk": {
                    "max_risk_per_trade_pct": 0.50,
                    "max_daily_loss_pct": 2.0,
                    "max_account_drawdown_pct": 8.0,
                    "max_open_positions": 2,
                },

                "trading": {
                    "new_entries_enabled": True,
                    "expiry_trading_enabled": False,
                },

                "oi": {
                    "strike_range": 15,
                },
            },
            source="DASHBOARD",
            user_id="TEST_USER",
        )

        assert_true(
            result["changed"],
            "Multiple update should change config",
        )

        assert_equal(
            manager.get_setting(
                "risk",
                "max_risk_per_trade_pct",
            ),
            0.50,
            "Risk update failed",
        )

        assert_equal(
            manager.get_setting(
                "risk",
                "max_daily_loss_pct",
            ),
            2.0,
            "Daily loss update failed",
        )

        assert_equal(
            manager.get_setting(
                "risk",
                "max_account_drawdown_pct",
            ),
            8.0,
            "Drawdown update failed",
        )

        assert_equal(
            manager.get_setting(
                "trading",
                "expiry_trading_enabled",
            ),
            False,
            "Expiry setting update failed",
        )

        assert_equal(
            manager.get_setting(
                "oi",
                "strike_range",
            ),
            15,
            "OI strike range update failed",
        )

        new_version = manager.get_setting(
            "system",
            "config_version",
        )

        assert_equal(
            new_version,
            old_version + 1,
            "Atomic update should increment version once",
        )

        print(
            "Settings Changed :",
            len(result["changes"]),
        )

        print(
            "Config Version   :",
            new_version,
        )

        print(
            "✅ PASS — Multiple settings updated atomically"
        )


# ============================================================
# TEST 5 — INVALID RISK REJECTED
# ============================================================

def test_invalid_risk_rejected():

    print_header(
        "TEST 5 — INVALID RISK CONFIGURATION"
    )

    with tempfile.TemporaryDirectory() as temp_dir:

        manager = create_test_manager(
            temp_dir
        )

        original_value = manager.get_setting(
            "risk",
            "max_risk_per_trade_pct",
        )

        rejected = False

        try:

            manager.update_setting(
                "risk",
                "max_risk_per_trade_pct",
                99,
                source="DASHBOARD",
            )

        except ValueError as error:

            rejected = True

            print(
                "Rejected:",
                error,
            )

        assert_true(
            rejected,
            "Unsafe risk value was not rejected",
        )

        assert_equal(
            manager.get_setting(
                "risk",
                "max_risk_per_trade_pct",
            ),
            original_value,
            "Rejected update modified active configuration",
        )

        print(
            "✅ PASS — Unsafe risk rejected"
        )


# ============================================================
# TEST 6 — INVALID OI RELATIONSHIP REJECTED
# ============================================================

def test_invalid_oi_relationship():

    print_header(
        "TEST 6 — INVALID OI SNAPSHOT CONFIGURATION"
    )

    with tempfile.TemporaryDirectory() as temp_dir:

        manager = create_test_manager(
            temp_dir
        )

        rejected = False

        try:

            manager.update_many(
                {
                    "oi": {
                        "min_snapshot_gap_minutes": 10,
                        "max_snapshot_gap_minutes": 5,
                    }
                },
                source="DASHBOARD",
            )

        except ValueError as error:

            rejected = True

            print(
                "Rejected:",
                error,
            )

        assert_true(
            rejected,
            "Invalid OI relationship was accepted",
        )

        assert_equal(
            manager.get_setting(
                "oi",
                "min_snapshot_gap_minutes",
            ),
            1.0,
            "Failed update modified minimum gap",
        )

        assert_equal(
            manager.get_setting(
                "oi",
                "max_snapshot_gap_minutes",
            ),
            15.0,
            "Failed update modified maximum gap",
        )

        print(
            "✅ PASS — Invalid relationship rejected atomically"
        )


# ============================================================
# TEST 7 — PROTECTED SETTING
# ============================================================

def test_protected_setting():

    print_header(
        "TEST 7 — PROTECTED SYSTEM SETTING"
    )

    with tempfile.TemporaryDirectory() as temp_dir:

        manager = create_test_manager(
            temp_dir
        )

        rejected = False

        try:

            manager.update_setting(
                "system",
                "config_version",
                999,
                source="DASHBOARD",
            )

        except PermissionError as error:

            rejected = True

            print(
                "Rejected:",
                error,
            )

        assert_true(
            rejected,
            "Protected config version was editable",
        )

        print(
            "✅ PASS — Protected setting blocked"
        )


# ============================================================
# TEST 8 — BOOLEAN SAFETY
# ============================================================

def test_boolean_safety():

    print_header(
        "TEST 8 — BOOLEAN SAFETY"
    )

    with tempfile.TemporaryDirectory() as temp_dir:

        manager = create_test_manager(
            temp_dir
        )

        rejected = False

        try:

            manager.update_setting(
                "trading",
                "trading_enabled",
                "False",
                source="DASHBOARD",
            )

        except ValueError as error:

            rejected = True

            print(
                "Rejected:",
                error,
            )

        assert_true(
            rejected,
            "String boolean should not be accepted",
        )

        manager.update_setting(
            "trading",
            "trading_enabled",
            False,
            source="DASHBOARD",
        )

        assert_equal(
            manager.get_setting(
                "trading",
                "trading_enabled",
            ),
            False,
            "Actual boolean update failed",
        )

        print(
            "Trading Enabled:",
            manager.get_setting(
                "trading",
                "trading_enabled",
            ),
        )

        print(
            "✅ PASS — Boolean input protected"
        )


# ============================================================
# TEST 9 — AUDIT HISTORY
# ============================================================

def test_audit_history():

    print_header(
        "TEST 9 — CONFIGURATION AUDIT HISTORY"
    )

    with tempfile.TemporaryDirectory() as temp_dir:

        manager = create_test_manager(
            temp_dir
        )

        manager.update_setting(
            "risk",
            "max_risk_per_trade_pct",
            0.50,
            source="DASHBOARD",
            user_id="TEST_USER",
        )

        assert_true(
            os.path.exists(
                manager.history_file
            ),
            "Audit history file was not created",
        )

        with open(
            manager.history_file,
            "r",
            encoding="utf-8",
        ) as file:

            lines = [
                line.strip()
                for line in file
                if line.strip()
            ]

        assert_true(
            len(lines) >= 1,
            "Audit history is empty",
        )

        record = json.loads(
            lines[-1]
        )

        assert_equal(
            record["event"],
            "SETTING_UPDATED",
            "Incorrect audit event",
        )

        assert_equal(
            record["source"],
            "DASHBOARD",
            "Incorrect audit source",
        )

        assert_equal(
            record["new_value"],
            0.50,
            "Incorrect audit value",
        )

        print(
            "Audit Event  :",
            record["event"],
        )

        print(
            "Audit Source :",
            record["source"],
        )

        print(
            "New Value    :",
            record["new_value"],
        )

        print(
            "✅ PASS — Configuration change audited"
        )


# ============================================================
# TEST 10 — CONFIG SNAPSHOT
# ============================================================

def test_config_snapshot():

    print_header(
        "TEST 10 — CONFIGURATION SNAPSHOT"
    )

    with tempfile.TemporaryDirectory() as temp_dir:

        manager = create_test_manager(
            temp_dir
        )

        manager.update_setting(
            "risk",
            "max_risk_per_trade_pct",
            0.50,
            source="DASHBOARD",
        )

        snapshot = (
            manager.create_snapshot()
        )

        assert_true(
            "config_version" in snapshot,
            "Snapshot missing config version",
        )

        assert_true(
            "created_at" in snapshot,
            "Snapshot missing timestamp",
        )

        assert_true(
            "configuration" in snapshot,
            "Snapshot missing configuration",
        )

        assert_equal(
            snapshot[
                "configuration"
            ][
                "risk"
            ][
                "max_risk_per_trade_pct"
            ],
            0.50,
            "Snapshot contains incorrect risk",
        )

        print(
            "Snapshot Version:",
            snapshot["config_version"],
        )

        print(
            "Snapshot Risk   :",
            snapshot[
                "configuration"
            ][
                "risk"
            ][
                "max_risk_per_trade_pct"
            ],
        )

        print(
            "✅ PASS — Configuration snapshot created"
        )


# ============================================================
# RUN TEST SUITE
# ============================================================

def run_all_tests():

    tests = [
        test_default_configuration,
        test_dashboard_single_update,
        test_configuration_persistence,
        test_multiple_settings,
        test_invalid_risk_rejected,
        test_invalid_oi_relationship,
        test_protected_setting,
        test_boolean_safety,
        test_audit_history,
        test_config_snapshot,
    ]

    print()
    print("=" * 78)
    print(
        "🧪 THETA AI TRADER — CONFIG MANAGER TEST SUITE"
    )
    print("=" * 78)

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
                f"❌ CONFIG MANAGER TESTS FAILED "
                f"({passed}/{len(tests)} passed)"
            )
            print("=" * 78)

            raise

    print()
    print("=" * 78)
    print(
        f"✅ ALL CONFIG MANAGER TESTS PASSED "
        f"({passed}/{len(tests)})"
    )
    print(
        "🔒 CONFIGURATION LAYER READY FOR "
        "BACKEND INTEGRATION"
    )
    print("=" * 78)


if __name__ == "__main__":

    run_all_tests()